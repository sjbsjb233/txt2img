"""Graceful-shutdown reconciliation for in-flight jobs.

When the process exits — SIGTERM during a rolling restart, a panic, a
container kill — there's a small window where jobs may already be in
``RUNNING`` state but their executor task never reaches a terminal
transition. Per design doc §20 problem 7 the operational rule is:

    > 重启时正在 RUNNING 的 Job 状态如何处理？建议在 SIGTERM 时把
    > RUNNING 一律转 FAILED with reason=SERVER_RESTART，并退还配额。

This module owns that reconciliation. It runs in two places:

1. **Lifespan shutdown.** After ``scheduler.drain()`` returns we walk
   any ``RUNNING`` rows the drain timeout did not clear and force them
   to ``FAILED``. The drain itself politely waits for executors to
   finish, so under normal conditions there are zero rows left here.
2. **Lifespan startup.** Before the scheduler starts we sweep
   ``RUNNING`` rows that survived a previous crash. Without this they
   would sit forever — the in-memory worker that owned them is gone,
   no SSE event will ever fire, and the user's ``today_count`` would
   be permanently held against them.

Both entry points share :func:`force_fail_running_jobs`, which is
idempotent and safe to call when zero rows are stuck.
"""

from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import Job
from app.domain.job_lifecycle import (
    FAILED,
    InvalidTransition,
    JobLifecycle,
    JobNotFound,
    get_job_lifecycle,
)
from app.domain.quota_guard import QuotaGuard, get_quota_guard

logger = logging.getLogger("txt2img.graceful_shutdown")


SERVER_RESTART_REASON = "SERVER_RESTART"


async def force_fail_running_jobs(
    *,
    lifecycle: JobLifecycle | None = None,
    quota_guard: QuotaGuard | None = None,
    reason: str = SERVER_RESTART_REASON,
) -> int:
    """Transition every ``RUNNING`` job to ``FAILED`` and refund quota.

    Returns the number of jobs that were transitioned. Callers may log
    this so an operator can tell the difference between "clean shutdown,
    nothing to do" (returns 0) and "we left work on the table".

    The function is **best-effort**:

    - DB read failures are propagated (caller owns the lifespan; if the
      DB is gone we want the panic to be visible).
    - Per-row failures are logged and skipped, never aborted. A bad row
      should not stop us from reconciling the rest.
    - The quota refund is clamped at zero by ``QuotaGuard`` so refunding
      a job whose user already had ``today_count==0`` is safe.
    """
    lc = lifecycle or get_job_lifecycle()
    qg = quota_guard or get_quota_guard()

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job.hash_id, Job.user_id).where(Job.status == "RUNNING")
            )
        ).all()
    if not rows:
        return 0

    logger.warning(
        "graceful_shutdown: %d RUNNING job(s) require reconciliation", len(rows)
    )

    transitioned = 0
    for hash_id, user_id in _stable_rows(rows):
        try:
            await lc.transition(hash_id, FAILED, reason=reason)
        except InvalidTransition:
            # Another concurrent path (admin cancel, executor finishing
            # mid-shutdown) already moved the row off RUNNING. Nothing
            # for us to do.
            logger.info(
                "graceful_shutdown: job=%s already advanced past RUNNING", hash_id
            )
            continue
        except JobNotFound:
            logger.info("graceful_shutdown: job=%s vanished mid-sweep", hash_id)
            continue
        except Exception:
            logger.exception(
                "graceful_shutdown: job=%s could not be force-failed", hash_id
            )
            continue

        try:
            await qg.refund_usage(user_id)
        except Exception:
            logger.exception(
                "graceful_shutdown: quota refund failed for user=%s job=%s",
                user_id,
                hash_id,
            )
        transitioned += 1

    logger.warning(
        "graceful_shutdown: force-failed %d/%d RUNNING job(s) reason=%s",
        transitioned,
        len(rows),
        reason,
    )
    return transitioned


def _stable_rows(rows: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return ``rows`` sorted by ``hash_id`` for deterministic ordering.

    Stable order makes test assertions tractable and produces a
    predictable timeline.jsonl trace operators can diff after a
    crashed restart.
    """
    return sorted(((str(h), str(u)) for h, u in rows), key=lambda r: r[0])
