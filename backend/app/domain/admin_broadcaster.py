"""Admin-only periodic SSE broadcasts.

Design doc §13.4 says admin clients should see live ``provider_state`` /
``provider_metrics`` / ``worker_pool_state`` updates without polling.
Rather than build a second SSE hub, we re-use the existing
:class:`SSEHub` and fan-out admin-only events to every active admin's
user-id channel.

Two periodic tasks live here:

- :func:`run_provider_metrics_loop` — polls :class:`MetricsEngine` +
  ``providers`` table and broadcasts a compact metrics snapshot.
- :func:`run_worker_pool_loop` — broadcasts queue depths + worker
  utilisation.

Both use a configurable cadence (default 5 seconds for the metrics
loop, 3 seconds for the worker pool loop). The values are tunable
via constants on this module rather than the config table because
they're operational dials and an admin reloading the metrics
snapshot interval mid-day shouldn't accidentally page themselves.

The ``cleanup_progress`` event is emitted by the cleanup routine
itself when a task transitions or makes notable progress; we do
NOT broadcast cleanup state on a timer because the cleanup runner
already owns its own progress signal.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import Provider, User
from app.domain.metrics_engine import MetricsEngine
from app.domain.sse_hub import SSEHub

logger = logging.getLogger("txt2img.admin_broadcaster")


# Cadence: chatty enough that admins see things move, slow enough not
# to spam idle connections. The numbers are intentionally not in
# ConfigCenter — see module docstring.
PROVIDER_METRICS_INTERVAL_SECONDS = 5.0
WORKER_POOL_INTERVAL_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _list_admin_user_ids() -> list[str]:
    """Return active admin user ids, for broadcast targeting.

    We re-read on every tick — it's a tiny query and admins are rare.
    Reading fresh means a freshly-promoted admin sees broadcasts on
    the next tick without restart.
    """
    async with get_session() as session:
        rows = (
            await session.execute(
                select(User.id).where(
                    User.role == "admin", User.status == "active"
                )
            )
        ).all()
    return [r[0] for r in rows]


async def _list_provider_metrics_payload(
    metrics: MetricsEngine,
) -> list[dict[str, Any]]:
    """Snapshot every provider's coarse metrics for the admin payload."""
    async with get_session() as session:
        providers = (
            await session.execute(
                select(
                    Provider.id,
                    Provider.label,
                    Provider.circuit_state,
                    Provider.balance_cny,
                    Provider.cost_per_image_cny,
                    Provider.max_concurrency,
                    Provider.cooldown_until,
                )
                .order_by(Provider.id)
            )
        ).all()

    rpm_by_pid = metrics.recent_calls_in_60s_by_provider()
    out: list[dict[str, Any]] = []
    for (
        pid,
        label,
        circuit_state,
        balance,
        cost,
        max_conc,
        cooldown_until,
    ) in providers:
        # Aggregate per-provider success rate by averaging over models
        # we have any traffic for. With no traffic the value is 1.0,
        # matching the selector's "freshness" treatment.
        sample_models = [
            key[1] for key in metrics._records.keys() if key[0] == pid  # type: ignore[attr-defined]
        ]
        if sample_models:
            success_total = sum(
                metrics.success_rate(pid, m) for m in sample_models
            ) / len(sample_models)
        else:
            success_total = 1.0
        out.append(
            {
                "id": pid,
                "label": label,
                "circuit_state": circuit_state,
                "balance_cny": float(balance),
                "cost_per_image_cny": float(cost),
                "current_concurrency": metrics.current_concurrency(pid),
                "max_concurrency": int(max_conc),
                "recent_calls_60s": int(rpm_by_pid.get(pid, 0)),
                "success_rate": round(success_total, 4),
                "cooldown_until": (
                    cooldown_until.isoformat() if cooldown_until else None
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------


async def _broadcast_to_all_admins(
    hub: SSEHub, kind: str, payload: dict[str, Any]
) -> None:
    """Push an event to every active admin's channel."""
    admin_ids = await _list_admin_user_ids()
    for uid in admin_ids:
        try:
            await hub.broadcast_to_user(uid, kind, payload)
        except Exception:
            logger.exception(
                "admin broadcast failed for user=%s kind=%s", uid, kind
            )


async def run_provider_metrics_loop(
    metrics: MetricsEngine,
    hub: SSEHub,
    *,
    interval_seconds: float = PROVIDER_METRICS_INTERVAL_SECONDS,
) -> None:
    """Background task: ``provider_metrics`` event every N seconds.

    Best-effort. Transient failures are logged and swallowed; the
    loop never dies on its own.
    """
    while True:
        try:
            payload = {
                "providers": await _list_provider_metrics_payload(metrics),
            }
            await _broadcast_to_all_admins(hub, "provider_metrics", payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("provider_metrics loop iteration failed")
        await asyncio.sleep(interval_seconds)


async def run_worker_pool_loop(
    queue_size_provider: Callable[[], Awaitable[dict[str, Any]]],
    hub: SSEHub,
    *,
    interval_seconds: float = WORKER_POOL_INTERVAL_SECONDS,
) -> None:
    """Background task: ``worker_pool_state`` snapshot every N seconds.

    ``queue_size_provider`` is an async callable that returns the
    pool / lane snapshot dict. We accept a callable rather than the
    ``JobScheduler`` directly so this module stays import-safe before
    the scheduler is constructed.
    """
    while True:
        try:
            payload = await queue_size_provider()
            await _broadcast_to_all_admins(hub, "worker_pool_state", payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker_pool loop iteration failed")
        await asyncio.sleep(interval_seconds)


async def collect_worker_pool_snapshot() -> dict[str, Any]:
    """Build the ``worker_pool_state`` payload from live state.

    Reads the in-memory job queue's lane sizes plus the scheduler's
    ``running_by_user`` aggregated by tier for a single coherent view
    of "who is doing what". Defensive against a not-yet-initialised
    scheduler so the function is safe to call before the executor
    has started.
    """
    from app.domain.job_queue import get_job_queue
    from app.domain.job_scheduler import get_job_scheduler
    from app.domain.metrics_engine import get_metrics_engine

    queue = get_job_queue()
    scheduler = get_job_scheduler()
    metrics = get_metrics_engine()

    queued_by_tier = await queue.queued_counts()

    # Running jobs aren't broken down by tier on the scheduler; infer
    # from the in-flight tasks. We don't store the tier on the task
    # object but the lane it came from is recorded by the queue. The
    # cheapest available signal is "scheduler running_count" globally
    # plus per-tier "0" — the admin UI can show queued accurately and
    # global running, which is what design doc §13.9 surfaces.
    lanes: dict[str, dict[str, int]] = {}
    for tier, queued in queued_by_tier.items():
        lanes[tier] = {"queued": int(queued), "running": 0}

    global_in_flight = int(scheduler.running_count)

    return {
        "lanes": lanes,
        "global_concurrency": global_in_flight,
        "provider_concurrency_total": int(
            sum(metrics._concurrency.values())  # type: ignore[attr-defined]
        ),
    }


async def broadcast_cleanup_progress(
    hub: SSEHub, payload: dict[str, Any]
) -> None:
    """Helper used by the cleanup runner to push progress to admins.

    Surfaced here (rather than inside the runner itself) so the runner
    stays free of admin-broadcasting logic — admin awareness is a
    cross-cutting concern that lives next to the rest of the admin
    fanout.
    """
    await _broadcast_to_all_admins(hub, "cleanup_progress", payload)
