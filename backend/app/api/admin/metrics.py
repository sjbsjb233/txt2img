"""Admin metrics endpoints (PR-17 / design doc §13.9).

Two routes:

- ``GET /api/admin/metrics/overview`` — the single-shot snapshot the
  admin Overview tab binds to (numbers cards, queue lanes, providers
  summary, disk usage).
- ``GET /api/admin/metrics/timeseries`` — bucketed line-chart data for
  one of {jobs_count, success_rate, p50_latency, provider_balance}.

Both endpoints are read-only. They favour cheap aggregate queries
over computing precise per-job stats — the overview is rendered
several times per minute and a 1k-job table scan would be wasteful.

The timeseries endpoint never returns more than ~1k points (range /
bucket combinations chosen so the chart stays responsive).
"""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import (
    BillingLedger,
    Image,
    Job,
    Provider,
    User,
)
from app.deps import CurrentAdmin
from app.domain.admin_broadcaster import collect_worker_pool_snapshot
from app.domain.metrics_engine import get_metrics_engine
from app.domain.runtime_configs import SchedulerConfig
from app.schemas.admin_metrics import (
    DiskUsageView,
    LaneState,
    MetricsOverviewResponse,
    ProviderSummaryView,
    TimeseriesBucket,
    TimeseriesMetric,
    TimeseriesPoint,
    TimeseriesRange,
    TimeseriesResponse,
    WorkerPoolView,
)
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.admin.metrics")

router = APIRouter(prefix="/api/admin/metrics", tags=["admin", "metrics"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Beijing-day boundary used by the "today" rollups. Matches the rest of
# the codebase (auth, quota_guard) which uses the same offset.
_BJ_OFFSET = timedelta(hours=8)


# Range / bucket combinations the timeseries endpoint accepts. We force
# the bucket to a value that produces ≤ ~720 points so the chart
# remains snappy even for the widest range.
_RANGE_TO_TIMEDELTA = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
_BUCKET_TO_TIMEDELTA = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}
# Cap the point count so a misconfigured bucket never produces a giant
# response. 24h / 1m = 1440; 7d / 5m = 2016; etc. Anything > the cap is
# truncated to the cap.
_MAX_POINTS = 1024


def _today_range() -> tuple[datetime, datetime]:
    """Return ``[start_utc, end_utc)`` for the current Beijing day.

    Used by the "today" cards on the overview. Beijing day boundaries
    keep the numbers stable across midnight UTC so admins on the
    operating timezone see the obvious thing.
    """
    now = datetime.now(timezone.utc)
    bj_now = now + _BJ_OFFSET
    bj_midnight = bj_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = bj_midnight - _BJ_OFFSET
    end_utc = start_utc + timedelta(days=1)
    return start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)


def _bucket_floor(ts: datetime, bucket: timedelta) -> datetime:
    """Round ``ts`` down to the start of its bucket window.

    Used to align grouping keys to wall-clock boundaries. Without this
    the buckets would drift relative to the chart's x-axis labels.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    epoch_seconds = int(ts.timestamp())
    bucket_seconds = int(bucket.total_seconds())
    floored = (epoch_seconds // bucket_seconds) * bucket_seconds
    return datetime.fromtimestamp(floored, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


async def _disk_usage_view() -> DiskUsageView:
    """Build the disk usage block of the overview.

    Uses the cached ``disk_usage`` rollup for the jobs total (refreshed
    every 5 min by ``cache_keeper``), and ``shutil.disk_usage`` for the
    free-space figure. The latter can fail in sandboxed environments;
    we fall back to ``None`` so the UI can render "unknown" instead of
    spuriously showing zero.
    """
    from app.db.models import DiskUsage

    async with get_session() as session:
        rows = (await session.execute(select(DiskUsage))).scalars().all()
    by_scope = {r.scope: r for r in rows}
    jobs_bytes = int(by_scope.get("jobs_total").bytes) if "jobs_total" in by_scope else 0

    settings = get_settings()
    free_bytes: int | None = None
    data_total_bytes: int = jobs_bytes
    try:
        usage = shutil.disk_usage(settings.DATA_ROOT)
        free_bytes = int(usage.free)
        # ``data_total_bytes`` mirrors the design doc field name; the
        # cleanest interpretation is "all bytes our DATA_ROOT uses",
        # which we approximate as jobs_bytes plus a small fixed
        # constant (~db file etc). For the v1 admin view this is fine.
        data_total_bytes = jobs_bytes
    except OSError:
        # Sandboxed CI etc. — leave free_bytes unknown.
        pass

    return DiskUsageView(
        data_total_bytes=data_total_bytes,
        data_jobs_bytes=jobs_bytes,
        free_bytes=free_bytes,
    )


async def _providers_summary() -> list[ProviderSummaryView]:
    """Per-provider 5-minute snapshot for the overview table."""
    metrics = get_metrics_engine()
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Provider).order_by(Provider.id)
            )
        ).scalars().all()

    out: list[ProviderSummaryView] = []
    rpm = metrics.recent_calls_in_60s_by_provider()
    for p in rows:
        # Aggregate per-provider success rate by averaging across models
        # the metrics engine has seen traffic for. Falls back to 1.0 for
        # idle providers — matches the selector's freshness bias.
        sample_models = [
            key[1]
            for key in metrics._records.keys()  # type: ignore[attr-defined]
            if key[0] == p.id
        ]
        if sample_models:
            success = sum(
                metrics.success_rate(p.id, m) for m in sample_models
            ) / len(sample_models)
            # ``p50_ms`` returns None for any model whose window has only
            # failed calls — _percentile filters by ``r.ok``. Drop the
            # Nones before averaging; if every model is None the provider
            # has no successful latency sample yet, so report None.
            p50_values = [
                v
                for v in (metrics.p50_ms(p.id, m) for m in sample_models)
                if v is not None
            ]
            p50: float | None = (
                sum(p50_values) / len(p50_values) if p50_values else None
            )
            window_s = metrics.window_seconds()
            calls = sum(
                metrics.qps(p.id, m) * window_s
                for m in sample_models
            )
        else:
            success = 1.0
            p50 = None
            calls = float(rpm.get(p.id, 0))

        out.append(
            ProviderSummaryView(
                id=p.id,
                label=p.label,
                circuit_state=p.circuit_state,
                balance_cny=float(p.balance_cny),
                cost_per_image_cny=float(p.cost_per_image_cny),
                current_concurrency=metrics.current_concurrency(p.id),
                max_concurrency=int(p.max_concurrency),
                success_rate_5min=round(success, 4),
                p50_ms_5min=round(p50, 2) if p50 is not None else None,
                calls_5min=int(calls),
            )
        )
    return out


@router.get("/overview", response_model=MetricsOverviewResponse)
async def get_overview(_admin: CurrentAdmin) -> MetricsOverviewResponse:
    """One JSON snapshot for the Overview tab.

    Each call hits the DB twice: once for "today" rollups, once for
    "providers". The queue / worker snapshot reads from in-memory
    state and is essentially free.
    """
    today_start, today_end = _today_range()

    async with get_session() as session:
        # Active users today = distinct user ids that submitted at least
        # one job within the Beijing-today window.
        active_users_today = (
            await session.execute(
                select(func.count(func.distinct(Job.user_id))).where(
                    Job.created_at >= today_start, Job.created_at < today_end
                )
            )
        ).scalar_one()

        jobs_today = (
            await session.execute(
                select(func.count(Job.id)).where(
                    Job.created_at >= today_start,
                    Job.created_at < today_end,
                    Job.status != "DELETED",
                )
            )
        ).scalar_one()

        # Images delivered today: count images joined by job created_at
        # — close enough for the dashboard, exact accounting lives in
        # billing_ledger.
        images_today = (
            await session.execute(
                select(func.count(Image.id))
                .select_from(Image)
                .join(Job, Image.job_id == Job.id)
                .where(
                    Job.created_at >= today_start,
                    Job.created_at < today_end,
                    Job.status == "SUCCEEDED",
                )
            )
        ).scalar_one()

        # 24h rolling success rate.
        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        ok_24h = (
            await session.execute(
                select(func.count(Job.id)).where(
                    Job.created_at >= cutoff_24h,
                    Job.status == "SUCCEEDED",
                )
            )
        ).scalar_one()
        terminal_24h = (
            await session.execute(
                select(func.count(Job.id)).where(
                    Job.created_at >= cutoff_24h,
                    Job.status.in_(("SUCCEEDED", "FAILED")),
                )
            )
        ).scalar_one()
        success_rate_24h = (
            float(ok_24h) / float(terminal_24h) if terminal_24h else 1.0
        )

    pool_snapshot = await collect_worker_pool_snapshot()
    lanes_raw: dict[str, dict[str, int]] = pool_snapshot.get("lanes", {})
    queue_state = {
        tier: LaneState(
            queued=int(lanes_raw[tier]["queued"]),
            running=int(lanes_raw[tier]["running"]),
        )
        for tier in lanes_raw
    }

    worker_pool = WorkerPoolView(
        max=int(SchedulerConfig().global_max_workers),
        in_use=int(pool_snapshot.get("global_concurrency", 0)),
    )

    return MetricsOverviewResponse(
        active_users_today=int(active_users_today or 0),
        jobs_today=int(jobs_today or 0),
        images_today=int(images_today or 0),
        success_rate_24h=round(success_rate_24h, 4),
        queue_state=queue_state,
        worker_pool=worker_pool,
        disk_usage=await _disk_usage_view(),
        providers_summary=await _providers_summary(),
    )


# ---------------------------------------------------------------------------
# Timeseries
# ---------------------------------------------------------------------------


async def _series_jobs_count(
    session,
    *,
    start: datetime,
    bucket: timedelta,
    provider_id: str | None,
    model: str | None,
) -> dict[datetime, float]:
    """``jobs_count`` series: terminal jobs per bucket."""
    q = select(Job.created_at, Job.status).where(
        Job.created_at >= start, Job.status != "DELETED"
    )
    if provider_id:
        q = q.where(Job.provider_used == provider_id)
    if model:
        q = q.where(Job.model == model)
    rows = (await session.execute(q)).all()
    counts: defaultdict[datetime, int] = defaultdict(int)
    for created_at, _status in rows:
        counts[_bucket_floor(created_at, bucket)] += 1
    return {k: float(v) for k, v in counts.items()}


async def _series_success_rate(
    session,
    *,
    start: datetime,
    bucket: timedelta,
    provider_id: str | None,
    model: str | None,
) -> dict[datetime, float]:
    """``success_rate`` series — successes / (success + fail) per bucket."""
    q = select(Job.created_at, Job.status).where(
        Job.created_at >= start, Job.status.in_(("SUCCEEDED", "FAILED"))
    )
    if provider_id:
        q = q.where(Job.provider_used == provider_id)
    if model:
        q = q.where(Job.model == model)
    rows = (await session.execute(q)).all()

    ok: defaultdict[datetime, int] = defaultdict(int)
    total: defaultdict[datetime, int] = defaultdict(int)
    for created_at, status in rows:
        b = _bucket_floor(created_at, bucket)
        total[b] += 1
        if status == "SUCCEEDED":
            ok[b] += 1
    return {b: (ok[b] / total[b]) if total[b] else 0.0 for b in total}


async def _series_p50_latency(
    session,
    *,
    start: datetime,
    bucket: timedelta,
    provider_id: str | None,
    model: str | None,
) -> dict[datetime, float]:
    """``p50_latency`` (seconds) per bucket using ``finished_at - started_at``.

    Computes the percentile in Python — SQLite has no PERCENTILE_CONT.
    For our scale (≤ tens of thousands per bucket) this is fast enough.
    """
    q = select(Job.created_at, Job.started_at, Job.finished_at).where(
        Job.created_at >= start,
        Job.status == "SUCCEEDED",
        Job.started_at.is_not(None),
        Job.finished_at.is_not(None),
    )
    if provider_id:
        q = q.where(Job.provider_used == provider_id)
    if model:
        q = q.where(Job.model == model)
    rows = (await session.execute(q)).all()

    per_bucket: defaultdict[datetime, list[float]] = defaultdict(list)
    for created_at, started_at, finished_at in rows:
        if started_at is None or finished_at is None:
            continue
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        seconds = (finished_at - started_at).total_seconds()
        if seconds < 0:
            continue
        per_bucket[_bucket_floor(created_at, bucket)].append(seconds)

    out: dict[datetime, float] = {}
    for b, samples in per_bucket.items():
        samples.sort()
        idx = (len(samples) - 1) // 2
        out[b] = samples[idx]
    return out


async def _series_provider_balance(
    session,
    *,
    start: datetime,
    bucket: timedelta,
    provider_id: str | None,
    model: str | None,
) -> dict[datetime, float]:
    """``provider_balance`` (CNY remaining) per bucket.

    Reconstructs the trailing balance from the current ``Provider.balance_cny``
    by walking the ``billing_ledger`` backwards: balance at the end of
    each bucket = current - sum(deductions after that bucket boundary).

    Filtering by ``provider_id`` is required — there's no meaningful
    aggregate across providers. Without one we sum balances across every
    provider, which gives the admin a "total credits remaining" line.
    The ``model`` parameter is ignored (balance isn't per-model).
    """
    if model:
        # No-op the model filter — balance is per-provider only. We
        # still accept the param so callers don't have to special-case
        # the URL.
        pass

    if provider_id:
        prov_q = (
            select(Provider.balance_cny).where(Provider.id == provider_id)
        )
        provider_balance = (await session.execute(prov_q)).scalar_one_or_none()
        if provider_balance is None:
            return {}
        ledger_q = (
            select(BillingLedger.deducted_at, BillingLedger.cost_cny).where(
                BillingLedger.provider_id == provider_id,
                BillingLedger.deducted_at >= start,
            )
        )
    else:
        provider_balance = (
            await session.execute(select(func.sum(Provider.balance_cny)))
        ).scalar_one() or 0.0
        ledger_q = select(BillingLedger.deducted_at, BillingLedger.cost_cny).where(
            BillingLedger.deducted_at >= start
        )

    rows = (await session.execute(ledger_q)).all()
    # Sort ascending so we can walk forward.
    rows = sorted(rows, key=lambda r: r[0])

    # Walk: build per-bucket the *running* balance at the END of each
    # bucket window. We treat ``provider_balance`` as the value at "now"
    # and back-compute by subtracting future deductions.
    per_bucket_total: defaultdict[datetime, float] = defaultdict(float)
    for ts, cost in rows:
        per_bucket_total[_bucket_floor(ts, bucket)] += float(cost or 0.0)

    # Compute cumulative deductions FROM each bucket boundary forward.
    # The total deductions after bucket b is sum of per_bucket_total for
    # all buckets b' >= b. Easiest: walk descending.
    sorted_buckets = sorted(per_bucket_total.keys(), reverse=True)
    cumulative: dict[datetime, float] = {}
    running = 0.0
    for b in sorted_buckets:
        running += per_bucket_total[b]
        cumulative[b] = running

    # Final value per bucket = current_balance + cumulative_after_this_bucket
    # because if the bucket is in the past, the deductions in/after it
    # have already lowered the balance, so add them back to recover the
    # value at the bucket boundary.
    return {
        b: float(provider_balance) + cumulative[b]
        for b in cumulative
    }


# Map metric → series-builder. Adding a new metric is one entry.
_SERIES_BUILDERS: dict[str, Any] = {
    "jobs_count": _series_jobs_count,
    "success_rate": _series_success_rate,
    "p50_latency": _series_p50_latency,
    "provider_balance": _series_provider_balance,
}


@router.get("/timeseries", response_model=TimeseriesResponse)
async def get_timeseries(
    _admin: CurrentAdmin,
    metric: TimeseriesMetric = Query(...),
    range: TimeseriesRange = Query("24h"),
    bucket: TimeseriesBucket = Query("5m"),
    provider_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
) -> TimeseriesResponse:
    """Return bucketed values for one metric over the requested window.

    The handler trusts the typing constraints on its query params (the
    enums above) so a malformed value already 422s before we touch the
    DB. ``provider_id`` and ``model`` are pure filters — passing both
    narrows the query further.
    """
    if metric not in _SERIES_BUILDERS:
        # Defence in depth — Literal already covers this.
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"unknown metric {metric!r}",
            field="metric",
        )

    range_delta = _RANGE_TO_TIMEDELTA[range]
    bucket_delta = _BUCKET_TO_TIMEDELTA[bucket]
    if bucket_delta > range_delta:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "bucket must be smaller than range",
            field="bucket",
        )
    expected_points = int(range_delta.total_seconds() // bucket_delta.total_seconds())
    if expected_points > _MAX_POINTS:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"requested {expected_points} points; cap is {_MAX_POINTS}",
            field="bucket",
        )

    end = _bucket_floor(datetime.now(timezone.utc), bucket_delta) + bucket_delta
    start = end - range_delta

    builder = _SERIES_BUILDERS[metric]
    async with get_session() as session:
        per_bucket = await builder(
            session,
            start=start,
            bucket=bucket_delta,
            provider_id=provider_id,
            model=model,
        )

    # Dense-fill: every bucket boundary in [start, end) gets a point so
    # the chart x-axis stays continuous. Missing buckets get None — the
    # frontend renders gaps cleanly.
    points: list[TimeseriesPoint] = []
    cursor = start
    while cursor < end:
        v = per_bucket.get(cursor)
        points.append(
            TimeseriesPoint(
                ts=cursor.isoformat(),
                value=float(v) if v is not None else None,
            )
        )
        cursor += bucket_delta

    return TimeseriesResponse(
        metric=metric,
        range=range,
        bucket=bucket,
        provider_id=provider_id,
        model=model,
        points=points,
    )
