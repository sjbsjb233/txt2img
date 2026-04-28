"""Tests for the typed wrappers in ``app.domain.runtime_configs`` and
``app.domain.tier_config``.

The wrappers are deliberately thin so the surface here is small: do
they read the right keys, do they reflect ConfigCenter updates, does
``TierConfig`` resolve per-user overrides correctly.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Runtime config wrappers (read app.domain.config_center)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_config_reads_default_from_seed(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.runtime_configs import SchedulerConfig

    await seed.bootstrap()
    await get_config_center().load_from_db()

    cfg = SchedulerConfig()
    assert cfg.global_max_workers == 32
    assert cfg.min_share_per_lane == pytest.approx(0.05)
    assert cfg.deadline_promotion_seconds == 300


@pytest.mark.asyncio
async def test_scheduler_config_reflects_set_many_immediately(
    initialized_db: None,
) -> None:
    """Hot reload acceptance criterion: new value visible on next read."""
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.runtime_configs import SchedulerConfig

    await seed.bootstrap()
    cc = get_config_center()
    await cc.load_from_db()

    cfg = SchedulerConfig()
    assert cfg.global_max_workers == 32

    await cc.set_many({"scheduler.global_max_workers": 64})
    assert cfg.global_max_workers == 64


@pytest.mark.asyncio
async def test_provider_scoring_weights_dict(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.runtime_configs import ProviderScoringConfig

    await seed.bootstrap()
    await get_config_center().load_from_db()

    weights = ProviderScoringConfig().weights
    assert set(weights) == {"cost", "success", "latency", "load", "freshness"}
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_emergency_config_starts_all_off(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.runtime_configs import EmergencyConfig

    await seed.bootstrap()
    await get_config_center().load_from_db()

    em = EmergencyConfig()
    assert em.pause_generation is False
    assert em.pause_image_access is False
    assert em.block_new_member_login is False
    assert em.force_captcha_global is False


# ---------------------------------------------------------------------------
# TierConfig
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier_config_loads_four_tiers(initialized_db: None) -> None:
    from app.db import seed
    from app.domain.tier_config import VALID_TIERS, get_tier_config

    await seed.bootstrap()
    tc = get_tier_config()
    await tc.load_from_db()

    assert set(tc.all().keys()) == set(VALID_TIERS)
    assert tc.get("vip").weight == 8
    assert tc.get("free").soft_quota == 8
    assert tc.get("free").slo_p95_ms is None


@pytest.mark.asyncio
async def test_tier_config_get_unknown_tier_raises(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    tc = get_tier_config()
    await tc.load_from_db()

    with pytest.raises(KeyError):
        tc.get("platinum")


@pytest.mark.asyncio
async def test_effective_quotas_uses_tier_default_when_no_override(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import User
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    tc = get_tier_config()
    await tc.load_from_db()

    async with get_session() as session:
        session.add(
            User(
                id="u_default00001",
                username="alice_default",
                password_hash="$argon2id$v=19$m=65536,t=2,p=1$abc$def",
                role="user",
                tier="premium",
            )
        )

    async with get_session() as session:
        from sqlalchemy import select

        user = (
            await session.execute(
                select(User).where(User.id == "u_default00001")
            )
        ).scalar_one()

    soft, hard = tc.effective_quotas(user)
    # Premium defaults from §3.1.
    assert (soft, hard) == (50, 100)


@pytest.mark.asyncio
async def test_effective_quotas_honours_per_user_override(
    initialized_db: None,
) -> None:
    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import User
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    tc = get_tier_config()
    await tc.load_from_db()

    async with get_session() as session:
        session.add(
            User(
                id="u_override0001",
                username="alice_override",
                password_hash="$argon2id$v=19$m=65536,t=2,p=1$abc$def",
                role="user",
                tier="free",
                override_soft_quota=999,
                # override_hard_quota intentionally NULL → falls back to tier
            )
        )

    async with get_session() as session:
        from sqlalchemy import select

        user = (
            await session.execute(
                select(User).where(User.id == "u_override0001")
            )
        ).scalar_one()

    soft, hard = tc.effective_quotas(user)
    # soft is overridden, hard falls back to free's default of 10.
    assert (soft, hard) == (999, 10)
