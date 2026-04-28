"""Tests for ``app.domain.provider_selector.ProviderSelector``.

The selector composes DB state, the metrics engine and the circuit
breaker. Each test seeds just the rows it needs and uses fresh
``MetricsEngine`` / ``CircuitBreaker`` instances injected into a fresh
``ProviderSelector`` so cross-test state cannot leak.
"""

from __future__ import annotations

import json

import pytest

from app.db.engine import get_session
from app.db.models import (
    Provider,
    ProviderModel,
    ProviderModelTierAccess,
    ProviderTierAccess,
    User,
)
from app.schemas.normalized import NormalizedRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(tier: str = "premium", uid: str = "u_test") -> User:
    """Pure ORM-shaped User without persisting it.

    The selector only needs ``id`` + ``tier`` so we don't INSERT into
    the users table here. This keeps tests focused and avoids tier /
    quota fixture noise.
    """
    return User(
        id=uid,
        username=uid,
        password_hash="x",
        role="user",
        tier=tier,
        status="active",
        display_name=uid,
        today_count=0,
        today_reset_date="2026-01-01",
    )


def _request(
    *,
    model: str = "gpt-image-2",
    n: int = 1,
    prompt: str = "hello",
    **kwargs,
) -> NormalizedRequest:
    return NormalizedRequest(model=model, prompt=prompt, n=n, **kwargs)


async def _seed_provider(
    *,
    pid: str,
    cost: float = 0.10,
    balance: float = 5.0,
    enabled: bool = True,
    state: str = "healthy",
    max_concurrency: int = 10,
    rpm_limit: int = 600,
    models: list[tuple[str, dict, bool]] | None = None,
    tiers: list[str] | None = None,
    pmta: list[tuple[str, str]] | None = None,
) -> None:
    """Seed one provider row + its model + tier-access rows.

    ``models`` is a list of ``(model_id, capabilities_dict, enabled)``;
    ``tiers`` is the list for ``provider_tier_access``; ``pmta`` is a
    list of ``(model_id, tier)`` for ``provider_model_tier_access``.
    """
    models = models or [("gpt-image-2", {}, True)]
    tiers = tiers if tiers is not None else ["vip", "premium", "standard", "free"]

    async with get_session() as session:
        session.add(
            Provider(
                id=pid,
                label=pid.upper(),
                adapter_type="openai_v1",
                base_url="https://example.com",
                api_key_enc="v1:fake",
                cost_per_image_cny=cost,
                initial_balance_cny=balance,
                balance_cny=balance,
                enabled=1 if enabled else 0,
                max_concurrency=max_concurrency,
                rpm_limit=rpm_limit,
                circuit_state=state,
            )
        )
        for model_id, caps, m_enabled in models:
            session.add(
                ProviderModel(
                    provider_id=pid,
                    model_id=model_id,
                    capabilities_json=json.dumps(caps),
                    enabled=1 if m_enabled else 0,
                )
            )
        for t in tiers:
            session.add(ProviderTierAccess(provider_id=pid, tier=t))
        for mid, t in pmta or []:
            session.add(
                ProviderModelTierAccess(
                    provider_id=pid,
                    model_id=mid,
                    tier=t,
                )
            )


def _selector(metrics=None, breaker=None):
    """Construct a fresh selector with isolated runtime services."""
    from app.domain.circuit_breaker import CircuitBreaker
    from app.domain.metrics_engine import MetricsEngine
    from app.domain.provider_selector import ProviderSelector

    m = metrics or MetricsEngine(window_seconds=300)
    b = breaker or CircuitBreaker()
    return ProviderSelector(metrics=m, breaker=b), m, b


async def _bootstrap() -> None:
    """Seed defaults (tiers + config) so the selector can read them."""
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    await get_config_center().load_from_db()
    await get_tier_config().load_from_db()


# ---------------------------------------------------------------------------
# Hard filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_provider_is_filtered(initialized_db: None) -> None:
    await _bootstrap()
    await _seed_provider(pid="p1", enabled=False)

    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    assert out == []


@pytest.mark.asyncio
async def test_disabled_model_row_is_filtered(initialized_db: None) -> None:
    """A provider with the model row disabled does not appear."""
    await _bootstrap()
    await _seed_provider(
        pid="p1", models=[("gpt-image-2", {}, False)]
    )
    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    assert out == []


@pytest.mark.asyncio
async def test_unsupported_model_is_filtered(initialized_db: None) -> None:
    await _bootstrap()
    await _seed_provider(
        pid="p1", models=[("gemini-3-pro-image-preview", {}, True)]
    )
    selector, _, _ = _selector()
    out = await selector.select(_user(), _request(model="gpt-image-2"))
    assert out == []


@pytest.mark.asyncio
async def test_tier_not_in_access_list_filtered(initialized_db: None) -> None:
    await _bootstrap()
    await _seed_provider(pid="p1", tiers=["vip"])
    selector, _, _ = _selector()
    out = await selector.select(_user(tier="free"), _request())
    assert out == []


@pytest.mark.asyncio
async def test_pmta_overrides_tier_access(initialized_db: None) -> None:
    """``provider_model_tier_access`` is the finer-grained whitelist.

    When any row exists for the (provider, model) pair the
    provider-level whitelist is ignored — design doc §4.1.
    """
    await _bootstrap()
    await _seed_provider(
        pid="p1",
        tiers=["vip", "premium"],  # would allow premium
        pmta=[("gpt-image-2", "vip")],  # but PMTA narrows to vip-only
    )
    selector, _, _ = _selector()

    # Premium is allowed by tier_access but rejected by PMTA.
    out = await selector.select(_user(tier="premium"), _request())
    assert out == []

    # VIP is in PMTA and passes.
    out_vip = await selector.select(_user(tier="vip"), _request())
    assert len(out_vip) == 1


@pytest.mark.asyncio
async def test_capabilities_match_n_max(initialized_db: None) -> None:
    """``n_max=1`` rejects ``n=2`` requests."""
    await _bootstrap()
    await _seed_provider(
        pid="p_strict",
        models=[("gpt-image-2", {"n_max": 1}, True)],
    )
    await _seed_provider(
        pid="p_loose",
        models=[("gpt-image-2", {"n_max": 4}, True)],
    )
    selector, _, _ = _selector()
    out = await selector.select(_user(), _request(n=2))
    pids = [c.provider.provider_id for c in out]
    assert "p_strict" not in pids
    assert "p_loose" in pids


@pytest.mark.asyncio
async def test_capabilities_match_size_list(initialized_db: None) -> None:
    """An explicit allow-list rejects sizes outside it."""
    await _bootstrap()
    await _seed_provider(
        pid="p_only_square",
        models=[
            (
                "gpt-image-2",
                {"size": ["1024x1024"]},
                True,
            )
        ],
    )
    selector, _, _ = _selector()

    out_ok = await selector.select(_user(), _request(size="1024x1024"))
    assert [c.provider.provider_id for c in out_ok] == ["p_only_square"]

    out_no = await selector.select(_user(), _request(size="1536x1024"))
    assert out_no == []


@pytest.mark.asyncio
async def test_capabilities_reject_transparent_bg_default(
    initialized_db: None,
) -> None:
    """``background='transparent'`` requires explicit support."""
    await _bootstrap()
    await _seed_provider(pid="p1")  # no transparent support
    selector, _, _ = _selector()
    out = await selector.select(
        _user(), _request(background="transparent")
    )
    assert out == []


@pytest.mark.asyncio
async def test_capabilities_reject_unsupported_boolean(
    initialized_db: None,
) -> None:
    """Boolean opt-in: user requests google_search but cap doesn't allow it."""
    await _bootstrap()
    await _seed_provider(
        pid="p1", models=[("gpt-image-2", {"google_search": False}, True)]
    )
    selector, _, _ = _selector()
    out = await selector.select(_user(), _request(google_search=True))
    assert out == []


@pytest.mark.asyncio
async def test_low_balance_is_dropped_and_marks_drained(
    initialized_db: None,
) -> None:
    """Sub-threshold balance → DRAINED in DB and removed from candidates."""
    from sqlalchemy import select

    await _bootstrap()
    # default threshold is 0.5
    await _seed_provider(pid="p_low", balance=0.1)
    await _seed_provider(pid="p_ok", balance=5.0)

    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    pids = [c.provider.provider_id for c in out]
    assert pids == ["p_ok"]

    # And the low-balance one was flipped to DRAINED.
    async with get_session() as session:
        state = (
            await session.execute(
                select(Provider.circuit_state).where(Provider.id == "p_low")
            )
        ).scalar_one()
    assert state == "drained"


@pytest.mark.asyncio
async def test_circuit_open_is_filtered(initialized_db: None) -> None:
    await _bootstrap()
    await _seed_provider(pid="p_open", state="open")
    await _seed_provider(pid="p_ok")

    selector, _, breaker = _selector()
    # Hydrate breaker mirror for p_open.
    assert await breaker.get_state("p_open") == "open"

    out = await selector.select(_user(), _request())
    assert [c.provider.provider_id for c in out] == ["p_ok"]


@pytest.mark.asyncio
async def test_all_providers_open_returns_empty(initialized_db: None) -> None:
    """Headline acceptance: every provider OPEN → no candidates."""
    await _bootstrap()
    await _seed_provider(pid="p1", state="open")
    await _seed_provider(pid="p2", state="open")

    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    assert out == []


@pytest.mark.asyncio
async def test_max_concurrency_filters(initialized_db: None) -> None:
    await _bootstrap()
    await _seed_provider(pid="p_busy", max_concurrency=2)

    _, m, b = _selector()
    selector, _, _ = _selector(metrics=m, breaker=b)
    m.lease_concurrency("p_busy")
    m.lease_concurrency("p_busy")
    # current_concurrency=2 == max_concurrency, should be filtered.

    out = await selector.select(_user(), _request())
    assert out == []


@pytest.mark.asyncio
async def test_rpm_limit_filters(initialized_db: None) -> None:
    await _bootstrap()
    await _seed_provider(pid="p_rpm", rpm_limit=3)

    _, m, b = _selector()
    selector, _, _ = _selector(metrics=m, breaker=b)
    # Pre-fill the metrics window with 3 calls in the last 60s.
    for _ in range(3):
        m.record_call("p_rpm", "gpt-image-2", ok=True, latency_ms=100)

    out = await selector.select(_user(), _request())
    assert out == []


# ---------------------------------------------------------------------------
# Soft scoring — single-dimension dominance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_dimension_prefers_cheaper(initialized_db: None) -> None:
    """1元 vs 0.5元 → 0.5元 排第一."""
    await _bootstrap()
    await _seed_provider(pid="p_expensive", cost=1.0)
    await _seed_provider(pid="p_cheap", cost=0.5)

    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    pids = [c.provider.provider_id for c in out]
    assert pids[0] == "p_cheap"


@pytest.mark.asyncio
async def test_success_rate_dimension_prefers_higher(
    initialized_db: None,
) -> None:
    """96% vs 80% success → 96% 排第一."""
    await _bootstrap()
    await _seed_provider(pid="p_a", cost=0.5)
    await _seed_provider(pid="p_b", cost=0.5)

    _, m, b = _selector()
    selector, _, _ = _selector(metrics=m, breaker=b)
    # p_a: 24/25 = 0.96 ; p_b: 80/100 = 0.80
    for _ in range(24):
        m.record_call("p_a", "gpt-image-2", ok=True, latency_ms=100)
    m.record_call("p_a", "gpt-image-2", ok=False, latency_ms=100)
    for _ in range(80):
        m.record_call("p_b", "gpt-image-2", ok=True, latency_ms=100)
    for _ in range(20):
        m.record_call("p_b", "gpt-image-2", ok=False, latency_ms=100)

    out = await selector.select(_user(), _request())
    pids = [c.provider.provider_id for c in out]
    assert pids[0] == "p_a"


@pytest.mark.asyncio
async def test_top_k_truncation(initialized_db: None) -> None:
    """Only ``fallback_top_k`` candidates returned even when more pass."""
    await _bootstrap()
    for i in range(5):
        await _seed_provider(pid=f"p_{i}", cost=0.5 + 0.01 * i)
    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    # Default fallback_top_k = 3.
    assert len(out) == 3


@pytest.mark.asyncio
async def test_select_uses_explicit_top_k_param(initialized_db: None) -> None:
    await _bootstrap()
    for i in range(5):
        await _seed_provider(pid=f"p_{i}", cost=0.5 + 0.01 * i)
    selector, _, _ = _selector()
    out = await selector.select(_user(), _request(), top_k=2)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Score breakdown sanity checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_components_clamped_to_unit_interval(
    initialized_db: None,
) -> None:
    """All five components must end up in [0, 1]."""
    await _bootstrap()
    await _seed_provider(pid="p1", cost=0.5)

    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    assert len(out) == 1
    comps = out[0].components
    for k in ("cost", "success", "latency", "load", "freshness"):
        assert 0.0 <= comps[k] <= 1.0


@pytest.mark.asyncio
async def test_freshness_default_one_for_unused_provider(
    initialized_db: None,
) -> None:
    """A never-used provider gets the maximum freshness bonus."""
    await _bootstrap()
    await _seed_provider(pid="p1")
    selector, _, _ = _selector()
    out = await selector.select(_user(), _request())
    assert out[0].components["freshness"] == 1.0


# ---------------------------------------------------------------------------
# Large-input smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selector_handles_many_providers(initialized_db: None) -> None:
    """200-provider sanity: selection is correct on a larger pool.

    Wall-clock assertions tend to flake under CI load, so we
    deliberately don't assert elapsed time here — the test exists to
    catch behaviour regressions on the bulk path (e.g. an accidental
    N+1 over candidates that breaks the result ordering or the
    capabilities filter on a large input). The hard-filter bulk
    queries keep this O(N), but if a future refactor reintroduces a
    per-candidate ``await``, this test still passes — it would be
    the dedicated perf gate (a separate "slow" suite, not normal CI)
    that surfaces it.
    """
    await _bootstrap()
    for i in range(200):
        await _seed_provider(pid=f"p_{i:03d}", cost=0.1 + 0.001 * i)
    selector, _, _ = _selector()

    out = await selector.select(_user(), _request())
    assert len(out) == 3  # default top_k

    # Cheapest providers should win the top-3 since cost is the only
    # axis that varies in this fixture. p_000 is the cheapest.
    assert out[0].provider.provider_id == "p_000"
