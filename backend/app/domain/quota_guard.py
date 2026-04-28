"""Quota accounting for per-user daily generation limits.

PR-09 owns the "how many jobs may this user submit today?" logic from
design doc §3.4 / §7.1. The guard is intentionally small and
transaction-friendly:

* ``check_hard_quota_exceeded`` and ``check_soft_quota_exceeded`` read
  the user's effective quota through ``TierConfig``.
* ``record_usage`` increments ``users.today_count`` when a job is
  accepted into the queue.
* ``refund_usage`` decrements it for system-side failures / cancelled
  jobs, never below zero.
* Every write path performs the Beijing-date lazy reset first. A
  background sweep calls ``reset_stale_users`` once per minute as a
  safety net; the lazy reset is still the source of truth.

All methods accept an optional caller-managed ``AsyncSession`` so the
PR-13 job-create transaction can fold quota increment + job insert into
one commit. Without a supplied session the guard opens and commits its
own short unit of work.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import User
from app.domain.tier_config import get_tier_config

logger = logging.getLogger("txt2img.quota")


BEIJING_TZ = timezone(timedelta(hours=8))
RESET_SWEEP_INTERVAL_SECONDS = 60


class QuotaGuardError(RuntimeError):
    """Raised when quota accounting is asked to operate on a missing user."""


class UserLike(Protocol):
    id: str
    tier: str
    override_soft_quota: int | None
    override_hard_quota: int | None


def beijing_today() -> str:
    """Return today's date in the quota reset timezone (UTC+8)."""
    return datetime.now(BEIJING_TZ).date().isoformat()


def _effective_quotas(user: UserLike) -> tuple[int, int]:
    """Resolve per-user quota overrides over the tier defaults."""
    spec = get_tier_config().get(user.tier)
    soft = (
        user.override_soft_quota
        if user.override_soft_quota is not None
        else spec.soft_quota
    )
    hard = (
        user.override_hard_quota
        if user.override_hard_quota is not None
        else spec.hard_quota
    )
    return soft, hard


class QuotaGuard:
    """Read and mutate ``users.today_count`` safely."""

    async def check_hard_quota_exceeded(
        self,
        user: UserLike,
        *,
        session: AsyncSession | None = None,
    ) -> bool:
        """Return True when ``today_count >= effective_hard_quota``.

        The comparison happens after lazy-resetting stale counters. The
        caller should reject job creation with 429 ``HARD_QUOTA_EXCEEDED``
        when this returns True.
        """
        _, hard = _effective_quotas(user)
        count = await self.today_count(user.id, session=session)
        return count >= hard

    async def check_soft_quota_exceeded(
        self,
        user: UserLike,
        *,
        session: AsyncSession | None = None,
    ) -> bool:
        """Return True when ``today_count >= effective_soft_quota``.

        Soft quota never blocks admission; the access policy turns this
        into the ``SOFT_QUOTA_EXCEEDED`` flag so the worker can apply
        Turnstile / delay / probability-fail later.
        """
        soft, _ = _effective_quotas(user)
        count = await self.today_count(user.id, session=session)
        return count >= soft

    async def today_count(
        self,
        user_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Return today's count after applying the lazy reset."""

        async def _read(s: AsyncSession) -> int:
            await self._lazy_reset_user(user_id, s)
            value = (
                await s.execute(
                    select(User.today_count).where(User.id == user_id)
                )
            ).scalar_one_or_none()
            if value is None:
                raise QuotaGuardError(f"user {user_id!r} not found")
            return int(value)

        if session is None:
            async with get_session() as s:
                return await _read(s)
        return await _read(session)

    async def record_usage(
        self,
        user: UserLike | str,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Increment today's usage and return the new count.

        Call this only after the access gate has accepted the job. It
        deliberately does not re-check quota; doing so here would split
        policy from accounting and make PR-13 harder to reason about.
        """
        user_id = user if isinstance(user, str) else user.id

        async def _write(s: AsyncSession) -> int:
            await self._lazy_reset_user(user_id, s)
            row = (
                await s.execute(
                    update(User)
                    .where(User.id == user_id)
                    .values(today_count=User.today_count + 1)
                    .returning(User.today_count)
                )
            ).one_or_none()
            if row is None:
                raise QuotaGuardError(f"user {user_id!r} not found")
            return int(row[0])

        if session is None:
            async with get_session() as s:
                return await _write(s)
        return await _write(session)

    async def refund_usage(
        self,
        user: UserLike | str,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Decrement today's usage and return the new count.

        The counter is clamped at zero. A job submitted before midnight
        and refunded after the Beijing reset will not make the new day
        negative; the lazy reset runs first and the clamp keeps it at 0.
        """
        user_id = user if isinstance(user, str) else user.id

        async def _write(s: AsyncSession) -> int:
            await self._lazy_reset_user(user_id, s)
            row = (
                await s.execute(
                    update(User)
                    .where(User.id == user_id)
                    .values(
                        today_count=case(
                            (User.today_count > 0, User.today_count - 1),
                            else_=0,
                        )
                    )
                    .returning(User.today_count)
                )
            ).one_or_none()
            if row is None:
                raise QuotaGuardError(f"user {user_id!r} not found")
            return int(row[0])

        if session is None:
            async with get_session() as s:
                return await _write(s)
        return await _write(session)

    async def reset_stale_users(
        self,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Reset every user whose ``today_reset_date`` is not today.

        This is the background sweep used by the lifespan task. It is
        safe to run often; when every row is current the UPDATE matches
        zero rows.
        """
        today = beijing_today()

        async def _reset(s: AsyncSession) -> int:
            result = await s.execute(
                update(User)
                .where(User.today_reset_date != today)
                .values(today_count=0, today_reset_date=today)
            )
            return int(result.rowcount or 0)

        if session is None:
            async with get_session() as s:
                return await _reset(s)
        return await _reset(session)

    async def _lazy_reset_user(self, user_id: str, session: AsyncSession) -> None:
        today = beijing_today()
        await session.execute(
            update(User)
            .where(User.id == user_id, User.today_reset_date != today)
            .values(today_count=0, today_reset_date=today)
        )


async def run_quota_reset_loop(
    *,
    interval_seconds: int = RESET_SWEEP_INTERVAL_SECONDS,
) -> None:
    """Background safety net for daily quota resets.

    The loop intentionally keeps running after transient DB errors; a
    later sweep or any user's next quota write will repair stale rows.
    """
    guard = get_quota_guard()
    while True:
        try:
            changed = await guard.reset_stale_users()
            if changed:
                logger.info("quota reset sweep refreshed %d user row(s)", changed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("quota reset sweep failed; will retry")
        await asyncio.sleep(interval_seconds)


_instance: QuotaGuard | None = None


def get_quota_guard() -> QuotaGuard:
    """Return the process-wide quota guard singleton."""
    global _instance
    if _instance is None:
        _instance = QuotaGuard()
    return _instance


def reset_quota_guard_for_tests() -> None:
    """Drop the singleton between tests."""
    global _instance
    _instance = None
