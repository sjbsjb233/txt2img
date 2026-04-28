"""Domain-facing wrappers around the ConfigCenter cache.

Five small, single-purpose facades that expose a typed view of the
``config`` keys each subsystem needs. They are intentionally thin —
just call ``ConfigCenter.get_required(...)`` and cast — because the
real source of truth is the cache in ``app.domain.config_center``;
duplicating values here would invite drift.

Each wrapper is a frozen dataclass-style accessor instead of a
plain function so that:

1. Domain code can hold a typed reference (``self._scheduler_cfg``)
   and read fields like ``cfg.global_max_workers`` rather than chasing
   string keys around the codebase.
2. Tests can construct a wrapper around a stub dict for isolation.

There is no caching beyond the underlying ``ConfigCenter``: every
property goes back to the singleton, so an admin PATCH is reflected
at the next read with no explicit ``reload`` needed at this layer.
``TierConfig`` is the exception — it reads the ``tiers`` table, not
the ``config`` table, and therefore owns its own load/reload cycle
(see ``app.domain.tier_config``).
"""

from __future__ import annotations

from app.domain.config_center import ConfigCenter, get_config_center


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class SchedulerConfig:
    """Knobs consumed by ``app.domain.job_scheduler`` (PR-11)."""

    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def global_max_workers(self) -> int:
        return int(self._cc.get_required("scheduler.global_max_workers"))

    @property
    def min_share_per_lane(self) -> float:
        return float(self._cc.get_required("scheduler.min_share_per_lane"))

    @property
    def deadline_promotion_seconds(self) -> int:
        return int(self._cc.get_required("scheduler.deadline_promotion_seconds"))


# ---------------------------------------------------------------------------
# Soft penalty (consumed by app.domain.soft_penalty in PR-11)
# ---------------------------------------------------------------------------


class SoftPenaltyConfig:
    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def base_delay_seconds(self) -> float:
        return float(self._cc.get_required("soft_penalty.base_delay_seconds"))

    @property
    def max_delay_seconds(self) -> float:
        return float(self._cc.get_required("soft_penalty.max_delay_seconds"))

    @property
    def base_fail_probability(self) -> float:
        return float(self._cc.get_required("soft_penalty.base_fail_probability"))

    @property
    def max_fail_probability(self) -> float:
        return float(self._cc.get_required("soft_penalty.max_fail_probability"))

    @property
    def require_turnstile(self) -> bool:
        return bool(self._cc.get_required("soft_penalty.require_turnstile"))


# ---------------------------------------------------------------------------
# Provider scoring (consumed by app.domain.provider_selector in PR-10)
# ---------------------------------------------------------------------------


class ProviderScoringConfig:
    """Weights + windows for the §7.4 two-stage filter+score pipeline."""

    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def weights(self) -> dict[str, float]:
        """Return the five-dimensional weight vector as a plain dict.

        Order matches design doc §13.5; the dict shape is convenient
        for the score aggregator without forcing it to do five
        ``get_required`` calls.
        """
        cc = self._cc
        return {
            "cost": float(cc.get_required("provider_scoring.weights.cost")),
            "success": float(cc.get_required("provider_scoring.weights.success")),
            "latency": float(cc.get_required("provider_scoring.weights.latency")),
            "load": float(cc.get_required("provider_scoring.weights.load")),
            "freshness": float(cc.get_required("provider_scoring.weights.freshness")),
        }

    @property
    def metric_window_seconds(self) -> int:
        return int(self._cc.get_required("provider_scoring.metric_window_seconds"))

    @property
    def fallback_top_k(self) -> int:
        return int(self._cc.get_required("provider_scoring.fallback_top_k"))

    @property
    def max_retries_per_job(self) -> int:
        return int(self._cc.get_required("provider_scoring.max_retries_per_job"))


# ---------------------------------------------------------------------------
# Circuit breaker (consumed by app.domain.circuit_breaker in PR-10)
# ---------------------------------------------------------------------------


class CircuitBreakerConfig:
    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def failure_threshold(self) -> int:
        return int(self._cc.get_required("circuit_breaker.failure_threshold"))

    @property
    def initial_cooldown_seconds(self) -> int:
        return int(
            self._cc.get_required("circuit_breaker.initial_cooldown_seconds")
        )

    @property
    def max_cooldown_seconds(self) -> int:
        return int(self._cc.get_required("circuit_breaker.max_cooldown_seconds"))

    @property
    def half_open_probe_concurrency(self) -> int:
        return int(
            self._cc.get_required("circuit_breaker.half_open_probe_concurrency")
        )


# ---------------------------------------------------------------------------
# Emergency switches (consumed by access_policy + auth)
# ---------------------------------------------------------------------------


class EmergencyConfig:
    """Four big-red-button switches from design doc §13.6.

    Reads only — admins flip them via ``PATCH /api/admin/config``.
    """

    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def pause_generation(self) -> bool:
        return bool(self._cc.get_required("emergency.pause_generation"))

    @property
    def pause_image_access(self) -> bool:
        return bool(self._cc.get_required("emergency.pause_image_access"))

    @property
    def block_new_member_login(self) -> bool:
        return bool(self._cc.get_required("emergency.block_new_member_login"))

    @property
    def force_captcha_global(self) -> bool:
        return bool(self._cc.get_required("emergency.force_captcha_global"))


# ---------------------------------------------------------------------------
# Provider filter (consumed by app.domain.provider_selector in PR-10)
# ---------------------------------------------------------------------------


class ProviderFilterConfig:
    """Tiny wrapper around ``provider_filter.*``. Carved out so PR-06 / PR-10
    can hold a typed reference without each importing the raw key string."""

    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def balance_min_threshold(self) -> float:
        return float(self._cc.get_required("provider_filter.balance_min_threshold"))


# ---------------------------------------------------------------------------
# Retention / thumbnail (consumed by image_io + cache_keeper in PR-07/PR-16)
# ---------------------------------------------------------------------------


class RetentionConfig:
    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def job_retention_days(self) -> int:
        return int(self._cc.get_required("retention.job_retention_days"))


class ThumbnailConfig:
    def __init__(self, center: ConfigCenter | None = None) -> None:
        self._cc = center or get_config_center()

    @property
    def max_long_edge(self) -> int:
        return int(self._cc.get_required("thumbnail.max_long_edge"))

    @property
    def quality(self) -> int:
        return int(self._cc.get_required("thumbnail.quality"))
