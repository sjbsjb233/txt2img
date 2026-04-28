"""Soft-quota penalty policy.

Admission only tags a job with ``SOFT_QUOTA_EXCEEDED``. The executor applies
the intentionally unpleasant behavior here: captcha must already be verified,
the job waits, then it may probabilistically fail without touching an upstream
provider or provider balance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Protocol

from app.db.models import User
from app.domain.runtime_configs import SoftPenaltyConfig
from app.domain.tier_config import get_tier_config


class RandomLike(Protocol):
    def random(self) -> float: ...


@dataclass(frozen=True)
class SoftPenaltyPlan:
    """Resolved penalty knobs for one job."""

    require_turnstile: bool
    captcha_verified: bool
    delay_seconds: float
    fail_probability: float
    overage_ratio: float


class SoftPenalty:
    """Compute and roll the soft-quota penalty."""

    def __init__(
        self,
        *,
        config: SoftPenaltyConfig | None = None,
        rng: RandomLike | None = None,
    ) -> None:
        self._config = config or SoftPenaltyConfig()
        self._rng = rng or random.Random()

    def plan(self, user: User, flags: dict[str, Any], *, today_count: int) -> SoftPenaltyPlan:
        """Return the penalty plan for the user's current overage."""
        soft, hard = get_tier_config().effective_quotas(user)
        ratio = overage_ratio(today_count=today_count, soft_quota=soft, hard_quota=hard)
        base_delay = self._read_float("base_delay_seconds", 5.0)
        max_delay = self._read_float("max_delay_seconds", 15.0)
        base_fail = self._read_float("base_fail_probability", 0.3)
        max_fail = self._read_float("max_fail_probability", 0.7)

        return SoftPenaltyPlan(
            require_turnstile=self._read_bool("require_turnstile", True),
            captcha_verified=is_captcha_verified(flags),
            delay_seconds=lerp(base_delay, max_delay, ratio),
            fail_probability=lerp(base_fail, max_fail, ratio),
            overage_ratio=ratio,
        )

    def should_fail(self, fail_probability: float) -> bool:
        """Roll the probabilistic fail branch."""
        return self._rng.random() < max(0.0, min(1.0, fail_probability))

    def _read_float(self, name: str, default: float) -> float:
        try:
            return float(getattr(self._config, name))
        except KeyError:
            return default

    def _read_bool(self, name: str, default: bool) -> bool:
        try:
            return bool(getattr(self._config, name))
        except KeyError:
            return default


def overage_ratio(*, today_count: int, soft_quota: int, hard_quota: int) -> float:
    """Linear 0..1 ratio between soft and hard quota."""
    denom = max(1, hard_quota - soft_quota)
    ratio = (today_count - soft_quota) / denom
    return max(0.0, min(1.0, float(ratio)))


def lerp(start: float, end: float, ratio: float) -> float:
    return float(start + (end - start) * max(0.0, min(1.0, ratio)))


def is_soft_quota_job(flags: dict[str, Any]) -> bool:
    return flags.get("SOFT_QUOTA_EXCEEDED") is True


def is_captcha_verified(flags: dict[str, Any]) -> bool:
    return (
        flags.get("captcha_verified") is True
        or flags.get("CAPTCHA_VERIFIED") is True
        or flags.get("turnstile_verified") is True
    )


_instance: SoftPenalty | None = None


def get_soft_penalty() -> SoftPenalty:
    global _instance
    if _instance is None:
        _instance = SoftPenalty()
    return _instance


def reset_soft_penalty_for_tests() -> None:
    global _instance
    _instance = None
