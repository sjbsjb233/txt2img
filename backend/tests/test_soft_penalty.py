from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.db.models import User
from app.domain.soft_penalty import (
    SoftPenalty,
    is_captcha_verified,
    overage_ratio,
)


@dataclass
class FixedRng:
    value: float

    def random(self) -> float:
        return self.value


def _user() -> User:
    return User(
        id="u_soft",
        username="soft",
        password_hash="x",
        role="user",
        tier="free",
        status="active",
    )


async def _bootstrap() -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    await get_config_center().load_from_db()
    await get_tier_config().load_from_db()


def test_overage_ratio_clamps_between_zero_and_one() -> None:
    assert overage_ratio(today_count=8, soft_quota=8, hard_quota=10) == 0.0
    assert overage_ratio(today_count=9, soft_quota=8, hard_quota=10) == 0.5
    assert overage_ratio(today_count=10, soft_quota=8, hard_quota=10) == 1.0
    assert overage_ratio(today_count=20, soft_quota=8, hard_quota=10) == 1.0


@pytest.mark.asyncio
async def test_soft_penalty_plan_interpolates_delay_and_fail_probability(
    initialized_db: None,
) -> None:
    await _bootstrap()
    penalty = SoftPenalty(rng=FixedRng(0.0))
    plan = penalty.plan(
        _user(),
        {"SOFT_QUOTA_EXCEEDED": True, "captcha_verified": True},
        today_count=9,
    )

    assert plan.overage_ratio == 0.5
    assert plan.delay_seconds == pytest.approx(10.0)
    assert plan.fail_probability == pytest.approx(0.5)
    assert plan.require_turnstile is True
    assert plan.captcha_verified is True


def test_soft_penalty_roll_uses_configured_probability() -> None:
    assert SoftPenalty(rng=FixedRng(0.29)).should_fail(0.3) is True
    assert SoftPenalty(rng=FixedRng(0.30)).should_fail(0.3) is False


def test_captcha_verified_accepts_known_flag_names() -> None:
    assert is_captcha_verified({"captcha_verified": True})
    assert is_captcha_verified({"CAPTCHA_VERIFIED": True})
    assert is_captcha_verified({"turnstile_verified": True})
    assert not is_captcha_verified({})
