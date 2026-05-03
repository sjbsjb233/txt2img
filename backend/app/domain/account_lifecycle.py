"""Account-deletion lifecycle background job.

Implements the timeline the Settings → Danger zone modal promises:

1. Admin approves a deletion request → user is set to ``disabled``
   immediately (handled in :mod:`app.api.admin.approvals`).
2. **T + 7 days** — the user row is *soft-deleted*: ``status='deleted'``,
   username renamed to ``<old>__deleted_<ts>`` so a fresh signup can
   reuse the name. This step is recoverable (admin can manually flip
   ``status`` back to active).
3. **T + 30 days** — the row and its data are *purged*: every
   ``jobs.<hash_id>`` directory under ``DATA_ROOT/jobs/`` is removed,
   then the user row itself is dropped. ``ON DELETE CASCADE`` on the
   FKs takes the rest (``user_preferences``, ``auth_sessions``,
   ``account_deletion_requests``, archive ``sessions``).

The job is idempotent: re-running it on the same day is a no-op
because the SQL filters consider the user's current ``status``.
``T`` is the ``resolved_at`` timestamp on the approved
``account_deletion_requests`` row, NOT the original ``requested_at``,
so the 7- and 30-day windows are anchored to the admin decision (not
to the user's intent).

The loop is started by ``app.main.lifespan``. Sleep cadence is 6 h —
the windows are coarse-grained so we don't need a tighter clock.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import (
    AccountDeletionRequest,
    Image,
    Job,
    User,
)
from app.services.image_io import jobs_root

logger = logging.getLogger("txt2img.account_lifecycle")

# ----- Window constants ------------------------------------------------------
# These are policy, not config — admins shouldn't tweak the soft-delete
# grace from a runtime knob (that would let a panicky operator skip the
# safety window). Override only via env in the future if there's
# a compliance reason.
SOFT_DELETE_DAYS = 7
PURGE_DAYS = 30

# Background loop period. 6h is well below the daily granularity of the
# windows (so we never miss a transition by more than one tick) while
# being cheap to run on a tiny SQLite.
LOOP_INTERVAL_SECONDS = 6 * 60 * 60


# ---------------------------------------------------------------------------
# Public sweep — exposed to admin endpoints + tests so they can trigger
# a one-shot run without waiting for the loop.
# ---------------------------------------------------------------------------


async def run_account_lifecycle_sweep(
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Advance every approved deletion request through the timeline.

    Returns a small report ``{soft_deleted: int, purged: int}`` so
    callers (admin debug endpoint, tests) can confirm what happened.
    """
    moment = now or datetime.now(timezone.utc)
    soft_cutoff = moment - timedelta(days=SOFT_DELETE_DAYS)
    purge_cutoff = moment - timedelta(days=PURGE_DAYS)

    async with get_session() as session:
        soft_deleted = await _soft_delete_due(session, soft_cutoff, moment)
        purged_user_ids, hash_ids = await _purge_due(
            session, purge_cutoff
        )

    # File deletion runs *outside* the session — shutil is blocking and
    # we don't want to hold the SQLite write lock while we walk dirs.
    if hash_ids:
        await _purge_files(hash_ids)

    if soft_deleted or purged_user_ids:
        logger.info(
            "account_lifecycle: soft_deleted=%d purged=%d",
            soft_deleted,
            len(purged_user_ids),
        )
    return {
        "soft_deleted": soft_deleted,
        "purged": len(purged_user_ids),
    }


# ---------------------------------------------------------------------------
# T+7 — soft delete
# ---------------------------------------------------------------------------


async def _soft_delete_due(
    session: AsyncSession, soft_cutoff: datetime, now: datetime
) -> int:
    """Flip every user with a ≥ 7d-old approved deletion to status='deleted'.

    The username gets the same ``<old>__deleted_<ts>`` rename used by
    the manual ``DELETE /api/admin/users/{id}`` path so a fresh signup
    can reuse the original name.

    Idempotent: requests whose target user is already ``deleted`` are
    excluded by the join's ``User.status != 'deleted'`` clause.
    """
    rows = (
        await session.execute(
            select(AccountDeletionRequest, User)
            .join(User, AccountDeletionRequest.user_id == User.id)
            .where(AccountDeletionRequest.status == "approved")
            .where(AccountDeletionRequest.resolved_at <= soft_cutoff)
            .where(User.status != "deleted")
        )
    ).all()

    if not rows:
        return 0

    ts = int(now.timestamp())
    count = 0
    for _req, user in rows:
        # Don't double-rename if a previous failed run already added
        # the suffix (defensive — should be impossible because we
        # filter status != 'deleted' above).
        if "__deleted_" not in user.username:
            user.username = f"{user.username}__deleted_{ts}"
        user.status = "deleted"
        count += 1
    return count


# ---------------------------------------------------------------------------
# T+30 — purge
# ---------------------------------------------------------------------------


async def _purge_due(
    session: AsyncSession, purge_cutoff: datetime
) -> tuple[list[str], list[str]]:
    """Hard-delete every user whose approval is ≥ 30d old.

    Returns ``(user_ids, hash_ids)``. ``hash_ids`` is the list of
    ``jobs.hash_id`` values whose on-disk directories should be
    rm -rf'd by :func:`_purge_files`.

    The ``users`` row itself is removed; FK ``ON DELETE CASCADE`` on
    user_preferences / auth_sessions / account_deletion_requests /
    sessions takes care of the dependent rows. Jobs and images do
    NOT cascade (the FK is plain ``user_id``), so we delete them
    explicitly — same for the disk directories.
    """
    rows = (
        await session.execute(
            select(AccountDeletionRequest, User)
            .join(User, AccountDeletionRequest.user_id == User.id)
            .where(AccountDeletionRequest.status == "approved")
            .where(AccountDeletionRequest.resolved_at <= purge_cutoff)
            .where(User.status == "deleted")
        )
    ).all()

    if not rows:
        return [], []

    user_ids: list[str] = []
    hash_ids: list[str] = []
    for _req, user in rows:
        # Collect every hash_id for this user before we drop the rows
        # (so we can rm -rf the disk dirs after the transaction).
        user_hash_ids = (
            await session.execute(
                select(Job.hash_id).where(Job.user_id == user.id)
            )
        ).scalars().all()
        hash_ids.extend(user_hash_ids)

        # Drop images (FK on jobs ON DELETE CASCADE handles them) by
        # deleting jobs. job_references / images / session_jobs all
        # cascade off jobs.
        await session.execute(
            delete(Job).where(Job.user_id == user.id)
        )
        # The user row itself — cascading FKs do the rest.
        await session.delete(user)
        user_ids.append(user.id)

    return user_ids, hash_ids


async def _purge_files(hash_ids: Iterable[str]) -> None:
    """Remove ``DATA_ROOT/jobs/<hash_id>/`` for every purged job.

    Wrapped in ``asyncio.to_thread`` because ``shutil.rmtree`` is
    blocking. We swallow per-directory errors — a missing dir is fine
    (the user might never have produced an image), and a permission
    error is logged so an operator can investigate without the loop
    grinding to a halt.
    """
    root = jobs_root()
    for hash_id in hash_ids:
        target = root / hash_id
        if not target.exists():
            continue
        try:
            await asyncio.to_thread(shutil.rmtree, target, True)
        except Exception:
            logger.exception(
                "account_lifecycle: failed to rmtree %s", target
            )


# ---------------------------------------------------------------------------
# Forever loop wired into the FastAPI lifespan
# ---------------------------------------------------------------------------


async def run_account_lifecycle_loop(
    interval_seconds: int = LOOP_INTERVAL_SECONDS,
) -> None:
    """Background loop. Cancellable by ``task.cancel()``.

    Runs one sweep right at startup so a process restart doesn't add
    up to 6 h of latency before due transitions land. Subsequent
    iterations sleep for ``interval_seconds``.
    """
    logger.info(
        "account_lifecycle: loop started, interval=%ss", interval_seconds
    )
    try:
        while True:
            try:
                await run_account_lifecycle_sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("account_lifecycle: sweep failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("account_lifecycle: loop cancelled")
        raise
