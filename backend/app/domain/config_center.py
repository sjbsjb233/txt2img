"""Process-wide hot-reloadable configuration store.

Backs every key listed in design doc §13.5. The schema, defaults and
range constraints live in this module; the ``config`` table is just
durable storage. Domain code reads via the small wrappers in
``app.domain.runtime_configs`` (and ``app.domain.tier_config`` for tier
rows), never by hitting the DB directly — that gives us a single,
auditable choke point for hot-reload.

Design notes:

- The cache is a plain in-memory dict, refreshed via ``load_from_db``
  on startup and incrementally updated by ``set_many``. A single uvicorn
  worker (design doc §1.4) keeps this safe; if we ever go multi-process
  we'll need pub/sub, but not before.
- ``set_many`` is the *only* mutation entry point. It validates the
  whole proposed delta atomically — so weight updates that would break
  the "weights sum to 1" invariant are rejected before any row touches
  disk. Per-key ``set`` is intentionally absent: cross-key invariants
  are common in this schema (max ≥ base, weights sum to 1) and a
  one-key-at-a-time API would force callers to disable validation to do
  routine edits.
- Validation surface is intentionally narrow: type + range per key, plus
  a few cross-key invariants. Anything richer (e.g. "scheduler workers
  must be a multiple of 4") would belong in the domain code that
  consumes the value, not here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import Config

logger = logging.getLogger("txt2img.config")


# ---------------------------------------------------------------------------
# Validator types
# ---------------------------------------------------------------------------


class ConfigValidationError(ValueError):
    """Raised when a proposed config update fails type / range / cross-key
    validation. The HTTP layer maps this to 422 ``INVALID_PARAMETER``.

    Attribute ``field`` carries the offending key (or pseudo-key for
    cross-key invariants like ``"provider_scoring.weights"``) so the
    error response can pinpoint what went wrong.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class _IntRange:
    min: int
    max: int

    def coerce(self, raw: Any, key: str) -> int:
        # Reject bools that Python would otherwise treat as 0/1: an admin
        # who types ``true`` for ``scheduler.global_max_workers`` is making
        # a real mistake we want to surface, not silently coerce.
        if isinstance(raw, bool):
            raise ConfigValidationError(
                f"{key}: expected integer, got boolean", field=key
            )
        if not isinstance(raw, int):
            raise ConfigValidationError(
                f"{key}: expected integer, got {type(raw).__name__}",
                field=key,
            )
        if raw < self.min or raw > self.max:
            raise ConfigValidationError(
                f"{key}: value {raw} out of range [{self.min}, {self.max}]",
                field=key,
            )
        return raw


@dataclass(frozen=True)
class _FloatRange:
    min: float
    max: float

    def coerce(self, raw: Any, key: str) -> float:
        if isinstance(raw, bool):
            raise ConfigValidationError(
                f"{key}: expected number, got boolean", field=key
            )
        if not isinstance(raw, (int, float)):
            raise ConfigValidationError(
                f"{key}: expected number, got {type(raw).__name__}",
                field=key,
            )
        v = float(raw)
        if v < self.min or v > self.max:
            raise ConfigValidationError(
                f"{key}: value {v} out of range [{self.min}, {self.max}]",
                field=key,
            )
        return v


@dataclass(frozen=True)
class _Bool:
    def coerce(self, raw: Any, key: str) -> bool:
        if not isinstance(raw, bool):
            raise ConfigValidationError(
                f"{key}: expected boolean, got {type(raw).__name__}",
                field=key,
            )
        return raw


# ---------------------------------------------------------------------------
# Schema: every key the system understands. Anything not listed here is
# rejected by ``set_many``. Keep alphabetised inside each section so the
# code reads next to design doc §13.5.
# ---------------------------------------------------------------------------


KNOWN_CONFIG_KEYS: dict[str, Any] = {
    # ----- scheduler -----
    "scheduler.global_max_workers": _IntRange(min=1, max=4096),
    "scheduler.min_share_per_lane": _FloatRange(min=0.0, max=1.0),
    "scheduler.deadline_promotion_seconds": _IntRange(min=0, max=24 * 3600),
    # ----- soft penalty -----
    "soft_penalty.base_delay_seconds": _FloatRange(min=0.0, max=600.0),
    "soft_penalty.max_delay_seconds": _FloatRange(min=0.0, max=600.0),
    "soft_penalty.base_fail_probability": _FloatRange(min=0.0, max=1.0),
    "soft_penalty.max_fail_probability": _FloatRange(min=0.0, max=1.0),
    "soft_penalty.require_turnstile": _Bool(),
    # ----- provider scoring -----
    "provider_scoring.weights.cost": _FloatRange(min=0.0, max=1.0),
    "provider_scoring.weights.success": _FloatRange(min=0.0, max=1.0),
    "provider_scoring.weights.latency": _FloatRange(min=0.0, max=1.0),
    "provider_scoring.weights.load": _FloatRange(min=0.0, max=1.0),
    "provider_scoring.weights.freshness": _FloatRange(min=0.0, max=1.0),
    "provider_scoring.metric_window_seconds": _IntRange(min=1, max=24 * 3600),
    "provider_scoring.fallback_top_k": _IntRange(min=1, max=20),
    "provider_scoring.max_retries_per_job": _IntRange(min=1, max=10),
    # ----- circuit breaker -----
    "circuit_breaker.failure_threshold": _IntRange(min=1, max=1000),
    "circuit_breaker.initial_cooldown_seconds": _IntRange(min=1, max=24 * 3600),
    "circuit_breaker.max_cooldown_seconds": _IntRange(min=1, max=24 * 3600),
    "circuit_breaker.half_open_probe_concurrency": _IntRange(min=1, max=64),
    # ----- provider filter -----
    "provider_filter.balance_min_threshold": _FloatRange(min=0.0, max=10_000.0),
    # ----- retention / thumbnail -----
    "retention.job_retention_days": _IntRange(min=1, max=3650),
    "thumbnail.max_long_edge": _IntRange(min=64, max=8192),
    "thumbnail.quality": _IntRange(min=1, max=100),
    # ----- emergency switches -----
    "emergency.pause_generation": _Bool(),
    "emergency.pause_image_access": _Bool(),
    "emergency.block_new_member_login": _Bool(),
    "emergency.force_captcha_global": _Bool(),
}


PROVIDER_SCORING_WEIGHT_KEYS: tuple[str, ...] = (
    "provider_scoring.weights.cost",
    "provider_scoring.weights.success",
    "provider_scoring.weights.latency",
    "provider_scoring.weights.load",
    "provider_scoring.weights.freshness",
)


# ---------------------------------------------------------------------------
# Cross-key invariants
# ---------------------------------------------------------------------------


_WEIGHT_SUM_TOLERANCE = 1e-6


def _validate_cross_key(merged: Mapping[str, Any]) -> None:
    """Run all cross-key invariants against the merged config view.

    Called by ``set_many`` with the *post-update* state; raises
    ``ConfigValidationError`` if any invariant is broken so the whole
    update is rejected before persistence.
    """
    weights_total = sum(float(merged[k]) for k in PROVIDER_SCORING_WEIGHT_KEYS)
    if abs(weights_total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise ConfigValidationError(
            f"provider_scoring.weights.* must sum to 1.0 (got {weights_total:.6f})",
            field="provider_scoring.weights",
        )

    base_delay = float(merged["soft_penalty.base_delay_seconds"])
    max_delay = float(merged["soft_penalty.max_delay_seconds"])
    if max_delay < base_delay:
        raise ConfigValidationError(
            "soft_penalty.max_delay_seconds must be >= soft_penalty.base_delay_seconds",
            field="soft_penalty.max_delay_seconds",
        )

    base_p = float(merged["soft_penalty.base_fail_probability"])
    max_p = float(merged["soft_penalty.max_fail_probability"])
    if max_p < base_p:
        raise ConfigValidationError(
            "soft_penalty.max_fail_probability must be >= soft_penalty.base_fail_probability",
            field="soft_penalty.max_fail_probability",
        )

    init_cd = int(merged["circuit_breaker.initial_cooldown_seconds"])
    max_cd = int(merged["circuit_breaker.max_cooldown_seconds"])
    if max_cd < init_cd:
        raise ConfigValidationError(
            "circuit_breaker.max_cooldown_seconds must be >= circuit_breaker.initial_cooldown_seconds",
            field="circuit_breaker.max_cooldown_seconds",
        )


# ---------------------------------------------------------------------------
# ConfigCenter singleton
# ---------------------------------------------------------------------------


class ConfigCenter:
    """In-memory mirror of the ``config`` table with validated updates.

    Concurrency: a single ``asyncio.Lock`` guards the cache+DB write
    path. Reads are lock-free against the cache dict because Python
    dict reads are atomic for our use; writes always replace the dict
    pointer rather than mutating in place.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    # -- lifecycle --------------------------------------------------------

    async def load_from_db(self) -> None:
        """Populate the in-memory cache from the ``config`` table.

        Tolerant of missing rows: any documented key not present in DB
        is left out of the cache. Callers can rely on
        ``app.db.seed.bootstrap`` having run first in production; tests
        that skip seeding will see a partial view.
        """
        async with get_session() as session:
            rows = (await session.execute(select(Config))).scalars().all()
        new_cache: dict[str, Any] = {}
        for row in rows:
            try:
                new_cache[row.key] = json.loads(row.value_json)
            except (ValueError, TypeError):
                logger.warning(
                    "config: skipping un-decodable value for key=%s", row.key
                )
        async with self._lock:
            self._cache = new_cache
            self._loaded = True
        logger.info("config: loaded %d keys", len(new_cache))

    async def reload(self) -> None:
        """Re-read the table; used after admin direct-DB edits in tests."""
        await self.load_from_db()

    # -- read -------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the cached value for ``key`` or ``default``.

        ``KNOWN_CONFIG_KEYS`` is the source of truth for which keys
        callers may ask about; passing an unknown key returns ``default``
        without raising so misspellings don't crash the scheduler.
        """
        return self._cache.get(key, default)

    def get_required(self, key: str) -> Any:
        """Return the cached value or raise if missing.

        Use from domain code that *must* have a value (e.g. scheduler
        worker count). Distinct from ``get`` so the failure mode is
        explicit at the call site.
        """
        if key not in self._cache:
            raise KeyError(f"config: required key not loaded: {key!r}")
        return self._cache[key]

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of every cached key.

        Used by ``GET /api/admin/config`` and tests. We copy so callers
        can iterate without worrying about a concurrent ``set_many``
        replacing the underlying dict.
        """
        return dict(self._cache)

    @property
    def loaded(self) -> bool:
        return self._loaded

    # -- write ------------------------------------------------------------

    async def set_many(
        self,
        updates: Mapping[str, Any],
        *,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate and persist a batch of config updates atomically.

        Returns the cleaned, post-coerce values (so callers can echo
        them back). Raises ``ConfigValidationError`` with no DB side
        effects if any check fails.

        ``actor_user_id`` is recorded in the ``updated_by`` column for
        audit. A separate ``audit_log`` row is the API layer's job
        (it has the IP address; we don't).
        """
        if not updates:
            return {}

        # 1. Per-key type/range coercion
        coerced: dict[str, Any] = {}
        for key, value in updates.items():
            spec = KNOWN_CONFIG_KEYS.get(key)
            if spec is None:
                raise ConfigValidationError(
                    f"unknown config key: {key!r}", field=key
                )
            coerced[key] = spec.coerce(value, key)

        async with self._lock:
            # 2. Cross-key invariants against the merged view (current
            #    cache overlaid with proposed changes).
            merged = {**self._cache, **coerced}
            _validate_cross_key(merged)

            # 3. Persist. SQLite ON CONFLICT upsert keeps this single-shot.
            async with get_session() as session:
                for key, value in coerced.items():
                    await _upsert_config_row(
                        session, key, value, actor_user_id
                    )

            # 4. Update cache last so a transaction failure leaves the
            #    cache untouched.
            self._cache = merged

        logger.info(
            "config: updated %d key(s) by actor=%s: %s",
            len(coerced),
            actor_user_id,
            sorted(coerced.keys()),
        )
        return coerced


async def _upsert_config_row(
    session: AsyncSession,
    key: str,
    value: Any,
    actor_user_id: str | None,
) -> None:
    """SQLite-flavoured upsert into ``config``.

    We use the dialect-specific ``ON CONFLICT(key)`` form rather than
    a SELECT-then-INSERT-or-UPDATE because the latter has a window
    where two callers can both INSERT and one will hit a PK violation.
    """
    payload = json.dumps(value, separators=(",", ":"))
    stmt = sqlite_insert(Config).values(
        key=key,
        value_json=payload,
        updated_by=actor_user_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Config.key],
        set_={
            "value_json": payload,
            "updated_by": actor_user_id,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: ConfigCenter | None = None


def get_config_center() -> ConfigCenter:
    """Return the process-wide ``ConfigCenter``.

    Created lazily on first access so importing this module from a
    test that wants its own instance is still cheap. The lifespan
    hook in ``app.main`` calls ``load_from_db`` once at startup.
    """
    global _instance
    if _instance is None:
        _instance = ConfigCenter()
    return _instance


def reset_config_center_for_tests() -> None:
    """Drop the singleton — for the per-test fresh-DB fixtures only."""
    global _instance
    _instance = None
