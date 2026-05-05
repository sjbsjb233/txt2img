"""Pydantic shapes for ``app.api.admin.jobs`` (Admin Job Inspector).

Each ``AdminJobInspect`` aggregates the user-facing JobDetail surface
plus six admin-only sub-objects: ``lifecycle`` / ``user_state`` /
``routing`` / ``attempts`` / ``circuit_ripples`` plus the bare
``provider_used`` / ``retries`` / ``cost_cny`` columns the user view
deliberately hides.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.archive import (
    JobImageSummary,
    JobReferenceSummary,
    JobSessionSummary,
    JobSetSummary,
    JobTiming,
)


# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------


class JobLifecycleSnapshot(BaseModel):
    """5-segment timing breakdown derived from ``jobs`` + attempt logs."""

    model_config = ConfigDict(extra="forbid")

    queued_at: datetime | None = None
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queued_seconds: float | None = None
    admission_seconds: float | None = None
    routing_seconds: float | None = None
    attempts_seconds: float | None = None
    finalize_seconds: float | None = None


class UserStateSnapshot(BaseModel):
    """User state at ``QUEUED → RUNNING``. Sourced from timeline.jsonl
    when the executor wrote it, falling back to the live DB row + tier
    config for legacy jobs."""

    model_config = ConfigDict(extra="forbid")

    tier: str
    today_count: int
    soft_quota_effective: int
    hard_quota_effective: int
    soft_quota_triggered: bool = False
    captcha_required: bool = False
    captcha_verified: bool = False
    recent_fail_rate_n: int = 0
    recent_fail_rate_total: int = 10


class FilteredProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    label: str
    reason: Literal[
        "disabled",
        "model_not_supported",
        "tier_denied",
        "capability_mismatch",
        "balance_low",
        "circuit_open",
        "circuit_drained",
        "concurrency_full",
        "rpm_full",
        "other",
    ]
    detail: str | None = None


class ScoredProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    provider_id: str
    label: str
    components: dict[str, float] = Field(default_factory=dict)
    total_score: float
    chosen: bool = False


class RoutingTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool_total: int
    pool_survived: int
    pool_scored: int
    filtered_out: list[FilteredProvider] = Field(default_factory=list)
    scored: list[ScoredProvider] = Field(default_factory=list)
    selector_config: dict[str, float] = Field(default_factory=dict)


class ProviderSnapshotAtAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    circuit_state: Literal[
        "healthy", "half_open", "open", "drained", "disabled"
    ] | None = None
    success_rate_5m: float | None = None
    p50_latency_ms: float | None = None
    current_concurrency: int | None = None
    max_concurrency: int | None = None
    current_rpm: int | None = None
    rpm_limit: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class AttemptDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_no: int
    provider_id: str
    provider_label: str
    started_at: datetime | None = None
    latency_ms: float | None = None
    ok: bool
    error_kind: Literal[
        "AUTH",
        "RATE_LIMITED",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_ERROR",
        "INVALID_PARAMETER",
        "UNSUPPORTED_MODEL",
        "NETWORK_ERROR",
        "EMPTY_RESPONSE",
        "OTHER",
    ] | None = None
    upstream_status: int | None = None
    upstream_body_excerpt: str | None = None
    provider_snapshot: ProviderSnapshotAtAttempt = Field(
        default_factory=ProviderSnapshotAtAttempt
    )
    raw_log_url: str
    chosen: bool = False


class CircuitRippleNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    label: str
    transition: str
    triggered_by_attempt: int


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


class AdminJobInspect(BaseModel):
    """Reply for ``GET /api/admin/jobs/<hash>/inspect``."""

    model_config = ConfigDict(extra="forbid")

    # ---- user-visible (mirrors JobDetail) ----
    hash_id: str
    seq_no: int
    status: str
    status_reason: str | None = None
    model: str
    model_display_name: str
    updated_at: datetime
    set_id: str | None = None
    session_id: str | None = None
    prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    references: list[JobReferenceSummary] = Field(default_factory=list)
    set: JobSetSummary | None = None
    images: list[JobImageSummary] = Field(default_factory=list)
    session: JobSessionSummary | None = None
    timing: JobTiming
    error: str | None = None
    flags: dict[str, Any] = Field(default_factory=dict)

    # ---- admin-only ----
    user_id: str
    user_username: str
    provider_used: str | None = None
    retries: int
    cost_cny: float
    balance_after_cny: float | None = None

    lifecycle: JobLifecycleSnapshot
    user_state_at_submit: UserStateSnapshot | None = None
    routing: RoutingTrace | None = None
    attempts: list[AttemptDetail] = Field(default_factory=list)
    circuit_ripples: list[CircuitRippleNote] = Field(default_factory=list)

    degraded_sections: list[str] = Field(default_factory=list)


class RequeueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_hash_id: str
    source_hash_id: str


__all__ = (
    "JobLifecycleSnapshot",
    "UserStateSnapshot",
    "FilteredProvider",
    "ScoredProvider",
    "RoutingTrace",
    "ProviderSnapshotAtAttempt",
    "AttemptDetail",
    "CircuitRippleNote",
    "AdminJobInspect",
    "RequeueResponse",
)
