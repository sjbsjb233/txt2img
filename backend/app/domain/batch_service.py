"""Batch lifecycle helpers — state machine, watchdog, SSE debounce.

Owns three concerns the API / lifecycle / watchdog all share:

1. **Terminal status resolution.** When the last bound Job lands a
   terminal state (or the watchdog fires), resolves the batch to the
   right terminal: ``completed`` (all SUCCEEDED), ``partial`` (any
   FAILED), ``cancelled`` (no FAILED but at least one CANCELLED), or
   ``abandoned`` (watchdog path).

2. **SSE ``batch_progress`` emission.** Coalesces bursts of updates per
   batch with a 50ms debounce window so a 12-job batch finishing in
   one tick produces *one* SSE push instead of twelve.

3. **Watchdog.** A 5-second loop scanning ``status='submitting'`` rows
   whose ``last_activity_at`` is older than the configured timeout
   (default 60s). Flips them to ``abandoned`` and fires the SSE
   notification. Idempotent under concurrent ``finalize_submission``
   calls — both paths use ``with_for_update`` and check the post-lock
   status before mutating.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import Batch, Job

logger = logging.getLogger("txt2img.batch_service")


# ---------------------------------------------------------------------------
# Status names — keep in lockstep with the SQL CHECK constraint.
# ---------------------------------------------------------------------------

SUBMITTING = "submitting"
RUNNING = "running"
COMPLETED = "completed"
PARTIAL = "partial"
CANCELLED = "cancelled"
ABANDONED = "abandoned"

NON_TERMINAL: frozenset[str] = frozenset({SUBMITTING, RUNNING})
TERMINAL: frozenset[str] = frozenset({COMPLETED, PARTIAL, CANCELLED, ABANDONED})


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_terminal_status(
    *,
    succeeded: int,
    failed: int,
    cancelled: int,
    total: int,
) -> str:
    """Pick the right terminal status for a batch's current counters.

    Rules (backend doc v0.3 §2.2):

    - ``failed > 0`` → ``partial`` (even when there are also successes
      and cancels — the "PR partially landed" semantics matter most to
      the user).
    - ``cancelled > 0`` and ``failed == 0`` → ``cancelled`` (every
      remaining job was either picked up successfully or explicitly
      cancelled).
    - ``succeeded == total`` → ``completed``.
    - Otherwise the bookkeeping is inconsistent; we fall back to
      ``partial`` as the safe default — surfaces the batch in the UI
      with the warning border so the user notices.
    """
    if failed > 0:
        return PARTIAL
    if cancelled > 0:
        return CANCELLED
    if succeeded == total:
        return COMPLETED
    return PARTIAL


def all_jobs_terminal(batch: Batch) -> bool:
    """True iff every counted Job has reached a terminal state."""
    return (
        batch.succeeded_count + batch.failed_count + batch.cancelled_count
        == batch.total_job_count
    )


# ---------------------------------------------------------------------------
# Locking helper
# ---------------------------------------------------------------------------


async def lock_batch(
    batch_id: str, session: AsyncSession
) -> Batch | None:
    """Return the row for ``batch_id`` under a write lock.

    Used by every path that mutates the row — ``_bind_to_batch`` in the
    jobs route, finalize / cancel handlers, the state-machine hook
    invoked when a Job lands terminal, and the watchdog. SQLite serial-
    ises writers through the WAL writer lock, so ``with_for_update`` is
    a no-op compatibility shim — but explicitly stating intent keeps the
    code portable should we ever migrate off SQLite.
    """
    stmt = select(Batch).where(Batch.id == batch_id).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# State machine — invoked from the lifecycle hook
# ---------------------------------------------------------------------------


async def on_job_terminal(
    *,
    batch_id: str,
    new_status: str,
    user_id: str,
) -> None:
    """Bump the per-batch counter for a Job that just reached terminal.

    Called from the lifecycle's broadcast path (see
    :func:`app.domain.job_lifecycle.JobLifecycle._broadcast`).

    Implementation note: under burst load (e.g. four sibling jobs of
    the same set finishing in the same tick), an ORM-style read-modify-
    write would race because SQLite silently drops ``with_for_update``.
    We therefore drive the counter bump via a single atomic
    ``UPDATE … SET <col> = <col> + 1 WHERE id = :id`` so SQLite's WAL
    writer lock serialises the increments naturally. The terminal-state
    resolution then happens in a second step against the post-update
    counters.
    """
    from app.db.models import Batch

    column_for_status = {
        "SUCCEEDED": Batch.succeeded_count,
        "FAILED": Batch.failed_count,
        "CANCELLED": Batch.cancelled_count,
    }
    column = column_for_status.get(new_status)
    if column is None:
        return
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        # Atomic single-row counter bump. ``synchronize_session=False``
        # tells SQLAlchemy not to invalidate ORM identity map entries —
        # we don't carry one for this row, so the no-op is correct and
        # avoids a useless extra query.
        result = await session.execute(
            update(Batch)
            .where(Batch.id == batch_id, Batch.user_id == user_id)
            .values(
                {
                    column: column + 1,
                    "updated_at": now,
                }
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            # Either the batch is gone or belongs to someone else —
            # both no-ops from this hook's point of view.
            return
        # Re-read post-update so we can decide if the batch as a whole
        # has now reached terminal.
        batch = (
            await session.execute(
                select(Batch).where(Batch.id == batch_id)
            )
        ).scalar_one_or_none()
        if batch is None:
            return
        if (
            batch.status not in TERMINAL
            and all_jobs_terminal(batch)
            and batch.submitted_count == batch.total_job_count
        ):
            batch.status = resolve_terminal_status(
                succeeded=batch.succeeded_count,
                failed=batch.failed_count,
                cancelled=batch.cancelled_count,
                total=batch.total_job_count,
            )
            batch.finalized_at = now
        snapshot = _snapshot_for_event(batch, in_flight_count=None)
    await get_batch_progress_emitter().publish(batch_id, user_id, snapshot)


# ---------------------------------------------------------------------------
# SSE emitter (debounced)
# ---------------------------------------------------------------------------


_DEBOUNCE_SECONDS = 0.05


class BatchProgressEmitter:
    """Coalesces ``batch_progress`` SSE pushes per batch.

    The hot path looks like this:

    1. The state-machine hook calls :meth:`publish` with the current
       row snapshot.
    2. The emitter records the latest snapshot under the batch id, then
       schedules a one-shot task that sleeps for ``_DEBOUNCE_SECONDS``
       and finally fires the SSE push with whatever snapshot is most
       recent at that point.
    3. If another :meth:`publish` lands inside the window, only the
       snapshot is updated — the existing scheduled task wakes up and
       delivers the freshest one.

    Failure mode: the SSE hub is best-effort, so any exception is
    swallowed and logged. Important: never let an emitter crash propa-
    gate up to the lifecycle's broadcast path.
    """

    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, dict[str, Any]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def publish(
        self,
        batch_id: str,
        user_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        async with self._lock:
            self._pending[batch_id] = (user_id, dict(payload))
            existing = self._tasks.get(batch_id)
            if existing is None or existing.done():
                self._tasks[batch_id] = asyncio.create_task(
                    self._run_after_debounce(batch_id)
                )

    async def _run_after_debounce(self, batch_id: str) -> None:
        try:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        async with self._lock:
            entry = self._pending.pop(batch_id, None)
            self._tasks.pop(batch_id, None)
        if entry is None:
            return
        user_id, payload = entry
        try:
            from app.domain.sse_hub import get_sse_hub

            await get_sse_hub().broadcast_to_user(
                user_id, "batch_progress", payload
            )
        except Exception:  # pragma: no cover — best-effort
            logger.exception(
                "batch_progress emit failed batch=%s", batch_id
            )

    async def flush_for_tests(self) -> None:
        """Wait for every scheduled debounce task. Tests-only."""
        async with self._lock:
            tasks = list(self._tasks.values())
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


_emitter: BatchProgressEmitter | None = None


def get_batch_progress_emitter() -> BatchProgressEmitter:
    global _emitter
    if _emitter is None:
        _emitter = BatchProgressEmitter()
    return _emitter


def reset_batch_progress_emitter_for_tests() -> None:
    global _emitter
    _emitter = None


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


async def run_watchdog_once() -> int:
    """Single sweep of the watchdog loop. Returns rows touched.

    Exposed for tests so they can fast-forward the simulated clock and
    drive the loop deterministically. The production loop just awaits
    this on a timer.
    """
    settings = get_settings()
    if not settings.BATCH_WATCHDOG_ENABLED:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.BATCH_ABANDONED_AFTER_SECONDS
    )
    touched = 0
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Batch)
                .where(
                    Batch.status == SUBMITTING,
                    Batch.last_activity_at < cutoff,
                )
                .with_for_update()
            )
        ).scalars().all()
        if not rows:
            return 0
        snapshots: list[tuple[Batch, dict[str, Any]]] = []
        for batch in rows:
            # Defensive: another path may have flipped status between
            # the SELECT and our claim of the lock — re-check.
            if batch.status != SUBMITTING:
                continue
            # If the user has already submitted every Job (just no
            # finalize call landed), promote rather than abandon —
            # mirrors backend doc v0.3 §6.1 watchdog/finalize race.
            if batch.submitted_count >= batch.total_job_count:
                if all_jobs_terminal(batch):
                    batch.status = resolve_terminal_status(
                        succeeded=batch.succeeded_count,
                        failed=batch.failed_count,
                        cancelled=batch.cancelled_count,
                        total=batch.total_job_count,
                    )
                    batch.finalized_at = datetime.now(timezone.utc)
                else:
                    batch.status = RUNNING
                batch.updated_at = datetime.now(timezone.utc)
            else:
                batch.status = ABANDONED
                batch.finalized_at = datetime.now(timezone.utc)
                batch.updated_at = batch.finalized_at
            touched += 1
            snapshots.append((batch, _snapshot_for_event(batch, in_flight_count=None)))
    # Emit after the commit so subscribers see the same status the DB now holds.
    for batch, payload in snapshots:
        await get_batch_progress_emitter().publish(
            batch.id, batch.user_id, payload
        )
    if touched:
        logger.info("batch watchdog: touched %d row(s)", touched)
    return touched


async def run_watchdog_loop() -> None:
    """Long-running asyncio task launched in the lifespan hook."""
    settings = get_settings()
    interval = settings.BATCH_WATCHDOG_INTERVAL_SECONDS
    while True:
        try:
            await asyncio.sleep(interval)
            await run_watchdog_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover — best-effort tick
            logger.exception("batch watchdog tick failed; continuing")


# ---------------------------------------------------------------------------
# Snapshots / counter computation
# ---------------------------------------------------------------------------


async def in_flight_count(batch_id: str, session: AsyncSession) -> int:
    """Count the QUEUED + RUNNING jobs bound to ``batch_id``."""
    from sqlalchemy import func

    value = (
        await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.batch_id == batch_id,
                Job.status.in_(("QUEUED", "RUNNING")),
            )
        )
    ).scalar_one()
    return int(value)


def _snapshot_for_event(
    batch: Batch, *, in_flight_count: int | None
) -> dict[str, Any]:
    """Build the payload the SSE event carries.

    ``in_flight_count`` is ``None`` from the lifecycle hook (we'd need
    a fresh COUNT and that adds load); the watchdog also passes
    ``None``. The frontend tolerates a missing ``in_flight_count``
    (treats it as "unknown, infer from succeeded/failed/total").
    """
    return {
        "type": "batch_progress",
        "batch_id": batch.id,
        "status": batch.status,
        "title": batch.title,
        "total_job_count": batch.total_job_count,
        "submitted_count": batch.submitted_count,
        "succeeded_count": batch.succeeded_count,
        "failed_count": batch.failed_count,
        "cancelled_count": batch.cancelled_count,
        "in_flight_count": in_flight_count,
        "updated_at": _isoformat(batch.updated_at),
        "finalized_at": _isoformat(batch.finalized_at),
    }


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Quota check
# ---------------------------------------------------------------------------


async def count_user_active_batches(user_id: str) -> int:
    """Return the user's count of non-terminal batches."""
    from sqlalchemy import func

    async with get_session() as session:
        value = (
            await session.execute(
                select(func.count())
                .select_from(Batch)
                .where(
                    Batch.user_id == user_id,
                    Batch.status.in_(tuple(NON_TERMINAL)),
                )
            )
        ).scalar_one()
        return int(value)


# Re-export so :func:`time.monotonic` is reachable through this module
# during tests that monkeypatch it.
_ = time
