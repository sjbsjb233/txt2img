"""Pydantic shapes for ``/api/admin/metrics/*`` (PR-17 / design doc §13.9).

Two endpoints:

- ``GET /api/admin/metrics/overview`` — single snapshot for the
  Overview tab cards (active users, jobs today, queue depths, providers
  summary, disk usage).
- ``GET /api/admin/metrics/timeseries`` — bucketed line-chart data for
  one of {jobs_count, success_rate, p50_latency, provider_balance}.

Both surfaces are read-only; the DB never moves on a metrics request.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# Names accepted by ``/timeseries?metric=...``. The narrow Literal forces
# the FastAPI handler to fan out to the right SQL — a free-form string
# would silently degrade to "unknown metric" 422 noise.
TimeseriesMetric = Literal[
    "jobs_count",
    "success_rate",
    "p50_latency",
    "provider_balance",
]
TimeseriesRange = Literal["24h", "7d", "30d"]
TimeseriesBucket = Literal["1m", "5m", "1h", "1d"]


class LaneState(BaseModel):
    """One row of the ``queue_state`` block — the depth of one tier lane."""

    queued: int
    running: int


class WorkerPoolView(BaseModel):
    max: int
    in_use: int


class DiskUsageView(BaseModel):
    """Disk usage sub-payload of ``/overview``.

    All sizes are bytes; the admin UI formats them client-side. ``free_bytes``
    comes from ``shutil.disk_usage`` on ``DATA_ROOT`` and is best-effort —
    if the call fails (e.g. inside a sandboxed CI container) we report
    ``None`` rather than 0 so the UI can show "unknown" instead of "full".
    """

    data_total_bytes: int
    data_jobs_bytes: int
    free_bytes: int | None


class ProviderSummaryView(BaseModel):
    id: str
    label: str
    circuit_state: str
    balance_cny: float
    cost_per_image_cny: float
    current_concurrency: int
    max_concurrency: int
    success_rate_5min: float
    p50_ms_5min: float | None
    calls_5min: int


class MetricsOverviewResponse(BaseModel):
    """Body of ``GET /api/admin/metrics/overview``."""

    active_users_today: int
    jobs_today: int
    images_today: int
    success_rate_24h: float
    queue_state: dict[str, LaneState]
    worker_pool: WorkerPoolView
    disk_usage: DiskUsageView
    providers_summary: list[ProviderSummaryView]


class TimeseriesPoint(BaseModel):
    """One data point on the line chart.

    ``ts`` is the bucket boundary — the *start* of the bucket window
    in ISO 8601 UTC. ``value`` is whatever the metric measures.
    """

    ts: str
    value: float | None


class TimeseriesResponse(BaseModel):
    metric: TimeseriesMetric
    range: TimeseriesRange
    bucket: TimeseriesBucket
    provider_id: str | None = None
    model: str | None = None
    points: list[TimeseriesPoint]
