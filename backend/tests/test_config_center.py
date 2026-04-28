"""Unit tests for ``app.domain.config_center.ConfigCenter``.

These exercise the validation surface and the in-memory cache path
without touching the FastAPI app: the goal is to fail fast on schema
regressions before they leak into the admin endpoints.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_from_db_populates_cache_after_seed(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.domain.config_center import (
        KNOWN_CONFIG_KEYS,
        get_config_center,
    )

    await seed.bootstrap()

    cc = get_config_center()
    await cc.load_from_db()

    snap = cc.snapshot()
    assert set(snap.keys()) == set(KNOWN_CONFIG_KEYS.keys())
    # Spot-check a few canonical defaults.
    assert snap["scheduler.global_max_workers"] == 32
    assert snap["soft_penalty.require_turnstile"] is True
    assert snap["provider_scoring.weights.cost"] == 0.30


@pytest.mark.asyncio
async def test_get_returns_default_for_unknown_key(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    # Unknown key — never raises, just returns the supplied default.
    assert cc.get("does.not.exist") is None
    assert cc.get("does.not.exist", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# set_many — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_many_persists_and_updates_cache(initialized_db: None) -> None:
    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import Config
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    accepted = await cc.set_many(
        {"scheduler.global_max_workers": 64},
        actor_user_id="u_admin000001",
    )
    assert accepted == {"scheduler.global_max_workers": 64}

    # Cache reflects the new value immediately, no reload needed.
    assert cc.get("scheduler.global_max_workers") == 64

    # DB also reflects it, with actor recorded in updated_by.
    async with get_session() as session:
        row = (
            await session.execute(
                select(Config).where(
                    Config.key == "scheduler.global_max_workers"
                )
            )
        ).scalar_one()
    assert json.loads(row.value_json) == 64
    assert row.updated_by == "u_admin000001"


@pytest.mark.asyncio
async def test_set_many_empty_is_noop(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()
    assert await cc.set_many({}) == {}


# ---------------------------------------------------------------------------
# set_many — validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_many_rejects_unknown_key(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import (
        ConfigValidationError,
        get_config_center,
    )

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    with pytest.raises(ConfigValidationError) as exc:
        await cc.set_many({"definitely.not.real": 1})
    assert exc.value.field == "definitely.not.real"


@pytest.mark.asyncio
async def test_set_many_rejects_wrong_type(initialized_db: None) -> None:
    """Boolean for an int field is a real mistake — we don't silent-coerce."""
    from app.db import seed
    from app.domain.config_center import (
        ConfigValidationError,
        get_config_center,
    )

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    with pytest.raises(ConfigValidationError):
        await cc.set_many({"scheduler.global_max_workers": True})


@pytest.mark.asyncio
async def test_set_many_rejects_out_of_range(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import (
        ConfigValidationError,
        get_config_center,
    )

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    with pytest.raises(ConfigValidationError):
        await cc.set_many({"scheduler.global_max_workers": 0})  # min=1
    with pytest.raises(ConfigValidationError):
        await cc.set_many({"thumbnail.quality": 200})  # max=100


@pytest.mark.asyncio
async def test_set_many_rejects_weights_not_summing_to_one(
    initialized_db: None,
) -> None:
    """Bumping cost without rebalancing the others must be rejected."""
    from app.db import seed
    from app.domain.config_center import (
        ConfigValidationError,
        get_config_center,
    )

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    with pytest.raises(ConfigValidationError) as exc:
        await cc.set_many({"provider_scoring.weights.cost": 0.9})
    assert exc.value.field == "provider_scoring.weights"


@pytest.mark.asyncio
async def test_set_many_accepts_full_weights_rebalance(
    initialized_db: None,
) -> None:
    """All five weights submitted in one PATCH that sums to 1.0 is fine."""
    from app.db import seed
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    new_weights = {
        "provider_scoring.weights.cost": 0.50,
        "provider_scoring.weights.success": 0.20,
        "provider_scoring.weights.latency": 0.15,
        "provider_scoring.weights.load": 0.10,
        "provider_scoring.weights.freshness": 0.05,
    }
    accepted = await cc.set_many(new_weights)
    assert sum(accepted.values()) == pytest.approx(1.0)
    assert cc.get("provider_scoring.weights.cost") == 0.50


@pytest.mark.asyncio
async def test_set_many_rejects_max_below_base_for_soft_penalty(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.domain.config_center import (
        ConfigValidationError,
        get_config_center,
    )

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    with pytest.raises(ConfigValidationError):
        # base default is 5 → max=2 violates max >= base.
        await cc.set_many({"soft_penalty.max_delay_seconds": 2})


@pytest.mark.asyncio
async def test_set_many_rejects_max_below_base_for_fail_probability(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.domain.config_center import (
        ConfigValidationError,
        get_config_center,
    )

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    with pytest.raises(ConfigValidationError):
        # base default is 0.3 → max=0.1 violates the invariant.
        await cc.set_many({"soft_penalty.max_fail_probability": 0.1})


@pytest.mark.asyncio
async def test_set_many_rolls_back_atomically_on_validation_error(
    initialized_db: None,
) -> None:
    """If any key in the batch is bad, no DB row gets touched."""
    from app.db import seed
    from app.domain.config_center import (
        ConfigValidationError,
        get_config_center,
    )

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    before = cc.get("scheduler.global_max_workers")

    with pytest.raises(ConfigValidationError):
        await cc.set_many(
            {
                "scheduler.global_max_workers": 64,  # valid
                "thumbnail.quality": 9999,  # invalid → whole batch rejected
            }
        )

    # Cache untouched.
    assert cc.get("scheduler.global_max_workers") == before


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_picks_up_external_db_edit(initialized_db: None) -> None:
    """Direct SQL edit (e.g. by an admin tool) becomes visible after reload."""
    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import Config
    from app.domain.config_center import get_config_center

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    async with get_session() as session:
        row = (
            await session.execute(
                select(Config).where(
                    Config.key == "scheduler.global_max_workers"
                )
            )
        ).scalar_one()
        row.value_json = json.dumps(128)

    # Cache still has the old value until reload runs.
    assert cc.get("scheduler.global_max_workers") == 32
    await cc.reload()
    assert cc.get("scheduler.global_max_workers") == 128
