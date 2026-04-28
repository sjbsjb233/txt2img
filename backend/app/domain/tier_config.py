"""Tier configuration cache.

Unlike the keys in ``app.domain.config_center`` this one reads from the
``tiers`` table — one row per tier — because the rows have rich
structure (six numeric fields plus an SLO) and admin edits typically
touch a few fields at once. A row-shaped table keeps that natural.

``TierConfig`` is the single read path for tier defaults. Per-user
overrides (``users.override_soft_quota`` / ``override_hard_quota``)
are layered on top by ``effective_quotas`` so callers don't have to
remember the precedence rule (override > tier default).

PR-04 ships read + reload only. PR-15 (admin user management) writes
to the per-user override columns; PR-09 (quota guard) consumes
``effective_quotas``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import Tier as TierRow

logger = logging.getLogger("txt2img.tier")


VALID_TIERS: tuple[str, ...] = ("vip", "premium", "standard", "free")


class UserQuotaView(Protocol):
    """Subset of ``User`` needed to resolve effective quotas."""

    tier: str
    override_soft_quota: int | None
    override_hard_quota: int | None


@dataclass(frozen=True)
class TierSpec:
    """Read-only snapshot of one row from ``tiers``."""

    tier: str
    weight: int
    max_concurrency: int
    max_queue: int
    soft_quota: int
    hard_quota: int
    slo_p95_ms: int | None


class TierConfig:
    """In-memory mirror of the four ``tiers`` rows.

    Reads are lock-free via dict lookup. Writes (``reload`` after admin
    PATCH) replace the dict reference atomically. Per-user override
    resolution is handled by ``effective_quotas`` so the call site
    doesn't have to know about the override columns.
    """

    def __init__(self) -> None:
        self._cache: dict[str, TierSpec] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    # -- lifecycle --------------------------------------------------------

    async def load_from_db(self, session: AsyncSession | None = None) -> None:
        """Refresh the in-memory cache from the ``tiers`` table.

        ``session`` is optional so admin PATCH can reuse its open
        session and avoid a second commit; in steady state we open
        our own.
        """
        new_cache: dict[str, TierSpec] = {}
        if session is None:
            async with get_session() as s:
                rows = (await s.execute(select(TierRow))).scalars().all()
        else:
            rows = (await session.execute(select(TierRow))).scalars().all()

        for row in rows:
            new_cache[row.tier] = TierSpec(
                tier=row.tier,
                weight=row.weight,
                max_concurrency=row.max_concurrency,
                max_queue=row.max_queue,
                soft_quota=row.soft_quota,
                hard_quota=row.hard_quota,
                slo_p95_ms=row.slo_p95_ms,
            )

        async with self._lock:
            self._cache = new_cache
            self._loaded = True
        logger.info("tier_config: loaded %d tier rows", len(new_cache))

    async def reload(self) -> None:
        await self.load_from_db()

    # -- read -------------------------------------------------------------

    def get(self, tier: str) -> TierSpec:
        """Return the spec for ``tier``.

        Raises ``KeyError`` if the tier name is unknown — every user
        row is constrained to one of the four valid tiers by DB CHECK
        so a missing entry here is a programming error worth surfacing.
        """
        spec = self._cache.get(tier)
        if spec is None:
            raise KeyError(f"unknown tier: {tier!r}")
        return spec

    def all(self) -> dict[str, TierSpec]:
        return dict(self._cache)

    @property
    def loaded(self) -> bool:
        return self._loaded

    # -- override resolution ---------------------------------------------

    def effective_quotas(self, user: UserQuotaView) -> tuple[int, int]:
        """Return ``(effective_soft, effective_hard)`` for one user.

        Precedence (design doc §3.2 / §13.2):
            override_*_quota (if not NULL) wins over tier default.

        We don't enforce ``hard >= soft`` here because the admin user
        write path is responsible for that invariant (PR-15); this
        function is read-only.
        """
        spec = self.get(user.tier)
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


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: TierConfig | None = None


def get_tier_config() -> TierConfig:
    global _instance
    if _instance is None:
        _instance = TierConfig()
    return _instance


def reset_tier_config_for_tests() -> None:
    global _instance
    _instance = None
