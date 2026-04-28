"""Admission policy for image-generation jobs.

This module implements PR-09's request gate from design doc §7.1. It is
separate from route handlers so PR-13 can call one domain function from
``POST /api/jobs`` and get the same decisions unit tests exercise here.

Evaluation order is intentionally fixed:

1. Emergency pause for generation.
2. Account status.
3. Hard quota.
4. User capacity (QUEUED + RUNNING >= tier max_concurrency + max_queue).
5. Soft quota flag.

Hard quota and user capacity both return 429, but with distinct codes;
the frontend uses those codes for different user copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.jobs_repository import JobsRepository, get_jobs_repository
from app.db.models import User
from app.domain.quota_guard import QuotaGuard, get_quota_guard
from app.domain.runtime_configs import EmergencyConfig
from app.domain.tier_config import get_tier_config
from app.utils.errors import api_error


@dataclass(frozen=True)
class AccessDecision:
    """Result of evaluating one user's generation admission."""

    passed: bool
    soft_quota_exceeded: bool = False
    flags: dict[str, bool] = field(default_factory=dict)
    http_status: int | None = None
    code: str | None = None
    message: str | None = None
    active_jobs: int | None = None
    active_capacity: int | None = None

    def raise_if_denied(self) -> None:
        """Raise the standard HTTP error for a denied decision."""
        if self.passed:
            return
        raise api_error(
            self.http_status or 403,
            self.code or "FORBIDDEN",
            self.message or "Request is not allowed.",
        )


class AccessPolicy:
    """Generation admission gate.

    ``model`` is accepted now even though PR-09 does not use it. PR-13's
    provider/capability gate needs the same signature, and keeping it
    here avoids a route-layer refactor later.
    """

    def __init__(
        self,
        *,
        emergency: EmergencyConfig | None = None,
        quota_guard: QuotaGuard | None = None,
        jobs_repository: JobsRepository | None = None,
    ) -> None:
        self._emergency = emergency or EmergencyConfig()
        self._quota_guard = quota_guard or get_quota_guard()
        self._jobs = jobs_repository or get_jobs_repository()

    async def evaluate(self, user: User, model: str | None = None) -> AccessDecision:
        """Return the admission decision for a would-be generation job."""
        _ = model  # Reserved for PR-13 provider/model availability checks.

        if self._emergency.pause_generation:
            return AccessDecision(
                passed=False,
                http_status=403,
                code="BLOCKED_BY_EMERGENCY",
                message="Service temporarily paused by admin.",
            )

        if user.status == "disabled":
            return AccessDecision(
                passed=False,
                http_status=403,
                code="ACCOUNT_DISABLED",
                message="Your account has been disabled.",
            )
        if user.status == "deleted":
            return AccessDecision(
                passed=False,
                http_status=401,
                code="UNAUTHORIZED",
                message="Account not found.",
            )

        if await self._quota_guard.check_hard_quota_exceeded(user):
            return AccessDecision(
                passed=False,
                http_status=429,
                code="HARD_QUOTA_EXCEEDED",
                message="Daily limit reached. Try again tomorrow.",
            )

        tier = get_tier_config().get(user.tier)
        capacity = tier.max_concurrency + tier.max_queue
        active_jobs = await self._jobs.count_active_by_user(user.id)
        if active_jobs >= capacity:
            return AccessDecision(
                passed=False,
                http_status=429,
                code="USER_BUSY",
                message="You have too many jobs in flight. Wait for some to finish.",
                active_jobs=active_jobs,
                active_capacity=capacity,
            )

        soft = await self._quota_guard.check_soft_quota_exceeded(user)
        flags = {"SOFT_QUOTA_EXCEEDED": True} if soft else {}
        return AccessDecision(
            passed=True,
            soft_quota_exceeded=soft,
            flags=flags,
            active_jobs=active_jobs,
            active_capacity=capacity,
        )

    async def enforce(self, user: User, model: str | None = None) -> AccessDecision:
        """Evaluate and raise a standard API error if denied."""
        decision = await self.evaluate(user, model=model)
        decision.raise_if_denied()
        return decision


_instance: AccessPolicy | None = None


def get_access_policy() -> AccessPolicy:
    """Return the process-wide access policy singleton."""
    global _instance
    if _instance is None:
        _instance = AccessPolicy()
    return _instance


def reset_access_policy_for_tests() -> None:
    """Drop the singleton between tests."""
    global _instance
    _instance = None
