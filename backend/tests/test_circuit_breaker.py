"""Tests for ``app.domain.circuit_breaker.CircuitBreaker``.

The breaker writes ``providers.circuit_state`` / ``cooldown_until`` so
every test that exercises a transition uses ``initialized_db``. Time is
injected via the ``time_source`` ctor arg so cooldown expiry doesn't
require ``asyncio.sleep``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import Provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_provider(provider_id: str = "p1", state: str = "healthy") -> None:
    async with get_session() as session:
        session.add(
            Provider(
                id=provider_id,
                label=provider_id.upper(),
                adapter_type="openai_v1",
                base_url="https://example.com",
                api_key_enc="v1:fake",
                cost_per_image_cny=0.1,
                initial_balance_cny=5.0,
                balance_cny=5.0,
                circuit_state=state,
            )
        )


async def _db_state(provider_id: str) -> tuple[str, object]:
    async with get_session() as session:
        row = (
            await session.execute(
                select(
                    Provider.circuit_state, Provider.cooldown_until
                ).where(Provider.id == provider_id)
            )
        ).one()
    return (row[0], row[1])


def _breaker(time_source=None):
    """Construct a fresh breaker for each test.

    The breaker is normally a process singleton (``get_circuit_breaker``)
    but tests instantiate it directly so each one gets clean in-memory
    state alongside the per-test DB.
    """
    from app.domain.circuit_breaker import CircuitBreaker
    from app.domain.runtime_configs import CircuitBreakerConfig

    return CircuitBreaker(
        config=CircuitBreakerConfig(),
        time_source=time_source,
    )


# ---------------------------------------------------------------------------
# Failure → OPEN transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_five_consecutive_failures_open_circuit(initialized_db: None) -> None:
    """Default ``failure_threshold`` is 5; the 5th failure trips OPEN."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    for i in range(4):
        state = await breaker.observe("p1", success=False)
        assert state == "healthy", f"attempt {i+1} should still be healthy"
    state = await breaker.observe("p1", success=False)
    assert state == "open"
    assert await breaker.get_state("p1") == "open"

    # DB must reflect the transition.
    db_state, cooldown = await _db_state("p1")
    assert db_state == "open"
    assert cooldown is not None  # cooldown timestamp persisted


@pytest.mark.asyncio
async def test_success_resets_failure_counter(initialized_db: None) -> None:
    """A success in the middle of a streak prevents OPEN."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    for _ in range(4):
        await breaker.observe("p1", success=False)
    await breaker.observe("p1", success=True)
    # Now another 4 failures should NOT trip — counter restarted at 0.
    for _ in range(4):
        await breaker.observe("p1", success=False)
    assert await breaker.get_state("p1") == "healthy"


# ---------------------------------------------------------------------------
# Cooldown expiry → HALF_OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_promotes_to_half_open(initialized_db: None) -> None:
    """Once ``cooldown_until`` passes, the next read returns HALF_OPEN."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    # Trip OPEN.
    for _ in range(5):
        await breaker.observe("p1", success=False)
    assert await breaker.get_state("p1") == "open"

    # Move past initial cooldown (default 30s).
    fake_t[0] = 1000.0 + 31.0
    assert await breaker.get_state("p1") == "half_open"


@pytest.mark.asyncio
async def test_half_open_success_returns_to_healthy(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    for _ in range(5):
        await breaker.observe("p1", success=False)
    fake_t[0] += 31.0
    # Acquire probe + report success: this is the canonical recovery path.
    async with breaker.acquire_probe("p1") as ok:
        assert ok is True
        state = await breaker.observe("p1", success=True)
    assert state == "healthy"
    assert await breaker.get_state("p1") == "healthy"
    db_state, cooldown = await _db_state("p1")
    assert db_state == "healthy"
    assert cooldown is None


@pytest.mark.asyncio
async def test_half_open_failure_doubles_cooldown(initialized_db: None) -> None:
    """Failed probe re-trips OPEN and doubles the cooldown."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    # First trip — initial cooldown 30s.
    for _ in range(5):
        await breaker.observe("p1", success=False)
    fake_t[0] += 31.0
    async with breaker.acquire_probe("p1") as ok:
        assert ok
        state = await breaker.observe("p1", success=False)
    assert state == "open"

    # Now the cooldown should be doubled to 60s.
    # Try after 31s — still open.
    fake_t[0] += 31.0
    assert await breaker.get_state("p1") == "open"
    # After another 30s (total 61s since re-trip), promote to half_open.
    fake_t[0] += 30.0
    assert await breaker.get_state("p1") == "half_open"


@pytest.mark.asyncio
async def test_cooldown_capped_at_max(initialized_db: None) -> None:
    """Doubling cannot exceed ``max_cooldown_seconds``."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()
    # Reduce the cap so the test runs in a small wall-clock window.
    await cc.set_many(
        {
            "circuit_breaker.initial_cooldown_seconds": 10,
            "circuit_breaker.max_cooldown_seconds": 25,
        }
    )

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    # Trip OPEN, fail probe twice — cooldown sequence: 10 → 20 → 25 (capped)
    for _ in range(5):
        await breaker.observe("p1", success=False)

    # Re-trip 1
    fake_t[0] += 11.0
    async with breaker.acquire_probe("p1"):
        await breaker.observe("p1", success=False)
    # Re-trip 2 — would be 40 but capped at 25.
    fake_t[0] += 21.0
    async with breaker.acquire_probe("p1"):
        await breaker.observe("p1", success=False)

    # Wait 24s (less than cap) — still open.
    fake_t[0] += 24.0
    assert await breaker.get_state("p1") == "open"
    # Wait 2 more seconds — promotes.
    fake_t[0] += 2.0
    assert await breaker.get_state("p1") == "half_open"


# ---------------------------------------------------------------------------
# Probe concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_one_probe_in_flight(initialized_db: None) -> None:
    """Default ``half_open_probe_concurrency=1`` allows only one probe."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    for _ in range(5):
        await breaker.observe("p1", success=False)
    fake_t[0] += 31.0

    async with breaker.acquire_probe("p1") as first:
        assert first is True
        async with breaker.acquire_probe("p1") as second:
            assert second is False  # slot taken


@pytest.mark.asyncio
async def test_probe_slot_released_on_exit(initialized_db: None) -> None:
    """After the probe context exits, a new probe can acquire the slot."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    for _ in range(5):
        await breaker.observe("p1", success=False)
    fake_t[0] += 31.0

    async with breaker.acquire_probe("p1") as ok:
        assert ok
        # Probe failed → state becomes OPEN; the next probe is gated by
        # the cooldown, not by the slot.
        await breaker.observe("p1", success=False)

    # Cooldown elapses — second probe should work.
    fake_t[0] += 61.0
    async with breaker.acquire_probe("p1") as ok2:
        assert ok2 is True


@pytest.mark.asyncio
async def test_acquire_probe_yields_false_when_healthy(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    breaker = _breaker()
    await _seed_provider("p1")

    async with breaker.acquire_probe("p1") as ok:
        assert ok is False


# ---------------------------------------------------------------------------
# Balance-driven DRAINED + admin reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_drained_persists_state(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    breaker = _breaker()
    await _seed_provider("p1")

    state = await breaker.mark_drained("p1")
    assert state == "drained"
    db_state, _ = await _db_state("p1")
    assert db_state == "drained"


@pytest.mark.asyncio
async def test_mark_drained_does_not_overwrite_open(
    initialized_db: None,
) -> None:
    """A faulty provider stays OPEN even if its balance dropped.

    Fault state is more important to surface to admin than balance
    state; recovery flows are different (topup vs reset-circuit).
    """
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    for _ in range(5):
        await breaker.observe("p1", success=False)
    assert await breaker.get_state("p1") == "open"

    # Now claim drained — should stay OPEN, not flip to DRAINED.
    state = await breaker.mark_drained("p1")
    assert state == "open"


@pytest.mark.asyncio
async def test_reset_to_healthy_admin(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    await _seed_provider("p1")

    for _ in range(5):
        await breaker.observe("p1", success=False)
    state = await breaker.reset_to_healthy("p1")
    assert state == "healthy"
    db_state, cooldown = await _db_state("p1")
    assert db_state == "healthy"
    assert cooldown is None


# ---------------------------------------------------------------------------
# Hydration from DB on first reference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_loaded_from_db_on_first_observe(
    initialized_db: None,
) -> None:
    """A row whose ``circuit_state='open'`` keeps that state when seen.

    Simulates a process restart: DB has OPEN; in-memory mirror is empty
    until first reference; first observe must NOT flip it back to
    HEALTHY just because consecutive_failures starts at 0.
    """
    from datetime import datetime, timedelta, timezone

    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    await get_config_center().load_from_db()

    # Persist OPEN with a cooldown 60s in the future.
    async with get_session() as session:
        session.add(
            Provider(
                id="p_persisted",
                label="P",
                adapter_type="openai_v1",
                base_url="https://example.com",
                api_key_enc="v1:x",
                cost_per_image_cny=0.1,
                initial_balance_cny=1.0,
                balance_cny=1.0,
                circuit_state="open",
                cooldown_until=datetime.now(timezone.utc) + timedelta(seconds=60),
            )
        )

    fake_t = [1000.0]
    breaker = _breaker(time_source=lambda: fake_t[0])
    state = await breaker.get_state("p_persisted")
    assert state == "open"
