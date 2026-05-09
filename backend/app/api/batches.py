"""``/api/batches`` — server-side batch lifecycle (frontend / backend doc v0.3).

Surface area mirrors the doc verbatim:

- ``POST /api/batches`` — register a batch (stage ①). Validates the
  spec, enforces the per-user concurrent-batch quota, and returns the
  batch summary the client renders as the optimistic top card.
- ``GET /api/batches`` — list non-terminal + recent-terminal batches.
- ``GET /api/batches/<id>`` — full detail with per-slot aggregation,
  feeding the detail drawer.
- ``POST /api/batches/<id>/finalize_submission`` — caller signals the
  fan-out is over so the row may transition out of ``submitting``.
- ``POST /api/batches/<id>/cancel`` — cancel every QUEUED job in the
  batch (RUNNING is left alone — see doc §3.5).
- ``DELETE /api/batches/<id>`` — drop the batch row; jobs preserved by
  default (``keep_jobs=true``).

Authorisation: ``CurrentUser`` gate + per-batch ownership check. Cross-
tenant access yields 404 (not 403) — same posture as
``app.api.sessions.delete_session``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import and_, delete, desc, func, or_, select, update

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import Batch, Job
from app.deps import CurrentUser
from app.domain.batch_service import (
    NON_TERMINAL,
    RUNNING,
    SUBMITTING,
    TERMINAL,
    all_jobs_terminal,
    count_user_active_batches,
    get_batch_progress_emitter,
    in_flight_count,
    resolve_terminal_status,
)
from app.domain.job_lifecycle import CANCELLED, QUEUED, get_job_lifecycle
from app.domain.job_queue import get_job_queue
from app.domain.quota_guard import get_quota_guard
from app.schemas.batches import (
    BatchActionResponse,
    BatchCreateRequest,
    BatchCreateResponse,
    BatchDetail,
    BatchListResponse,
    BatchSlotProgress,
    BatchSlotSpec,
    BatchSpec,
    BatchSummary,
)
from app.utils.audit import write_audit  # noqa: F401 — reserved for audit log
from app.utils.errors import api_error
from app.utils.ids import new_batch_id

logger = logging.getLogger("txt2img.batches")

router = APIRouter(prefix="/api/batches", tags=["batches"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _row_to_summary(row: Batch) -> BatchSummary:
    return BatchSummary(
        batch_id=row.id,
        title=row.title,
        status=row.status,  # type: ignore[arg-type]
        total_job_count=row.total_job_count,
        submitted_count=row.submitted_count,
        succeeded_count=row.succeeded_count,
        failed_count=row.failed_count,
        cancelled_count=row.cancelled_count,
        created_at=_aware(row.created_at),  # type: ignore[arg-type]
        updated_at=_aware(row.updated_at),  # type: ignore[arg-type]
        last_activity_at=_aware(row.last_activity_at),  # type: ignore[arg-type]
        finalized_at=_aware(row.finalized_at),
    )


async def _load_owned_batch(batch_id: str, user_id: str, session) -> Batch:
    row = (
        await session.execute(
            select(Batch).where(Batch.id == batch_id, Batch.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise api_error(404, "NOT_FOUND", "Batch not found.", field="batch_id")
    return row


async def _emit(batch: Batch, in_flight: int | None = None) -> None:
    payload: dict[str, Any] = {
        "type": "batch_progress",
        "batch_id": batch.id,
        "status": batch.status,
        "title": batch.title,
        "total_job_count": batch.total_job_count,
        "submitted_count": batch.submitted_count,
        "succeeded_count": batch.succeeded_count,
        "failed_count": batch.failed_count,
        "cancelled_count": batch.cancelled_count,
        "in_flight_count": in_flight,
        "updated_at": _aware(batch.updated_at).isoformat(  # type: ignore[union-attr]
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "finalized_at": (
            _aware(batch.finalized_at).isoformat(  # type: ignore[union-attr]
                timespec="seconds"
            ).replace("+00:00", "Z")
            if batch.finalized_at is not None
            else None
        ),
    }
    await get_batch_progress_emitter().publish(batch.id, batch.user_id, payload)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


async def _validate_spec_session_ownership(
    spec: BatchSpec, user_id: str
) -> None:
    """Make sure every ``session_id`` referenced in the spec belongs to the user."""
    candidates: set[str] = set()
    if spec.shared_session_id:
        candidates.add(spec.shared_session_id)
    for slot in spec.slots:
        if slot.session_id:
            candidates.add(slot.session_id)
    if not candidates:
        return
    from app.db.models import Session as SessionRow

    async with get_session() as session:
        rows = (
            await session.execute(
                select(SessionRow.id).where(
                    SessionRow.id.in_(candidates),
                    SessionRow.user_id == user_id,
                )
            )
        ).all()
        owned = {r[0] for r in rows}
    missing = candidates - owned
    if missing:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"session(s) not found or not owned: {sorted(missing)}",
            field="session_id",
        )


# ---------------------------------------------------------------------------
# POST /api/batches — register
# ---------------------------------------------------------------------------


@router.post("", response_model=BatchCreateResponse)
async def create_batch(
    body: BatchCreateRequest, user: CurrentUser
) -> BatchCreateResponse:
    """Register a fresh batch.

    Steps (backend doc v0.3 §3.1):

    1. Pydantic shape validation already happened.
    2. Cap-check for slots / total images via :mod:`config`.
    3. Cross-check that any ``session_id`` named inside ``spec`` belongs
       to the caller.
    4. Quota gate: user can have at most K non-terminal batches.
    5. INSERT the row, return the summary + spec.
    """
    settings = get_settings()
    if len(body.spec.slots) > settings.BATCH_SLOTS_MAX:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"slots exceed the cap ({len(body.spec.slots)} > {settings.BATCH_SLOTS_MAX})",
            field="spec.slots",
        )
    if body.total_job_count > settings.BATCH_TOTAL_IMAGES_MAX:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"total images exceed the cap ({body.total_job_count} > "
            f"{settings.BATCH_TOTAL_IMAGES_MAX})",
            field="total_job_count",
        )
    for slot in body.spec.slots:
        if slot.image_count > settings.BATCH_SLOT_IMAGE_COUNT_MAX:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"slot {slot.stable_idx} image_count {slot.image_count} > "
                f"{settings.BATCH_SLOT_IMAGE_COUNT_MAX}",
                field="spec.slots.image_count",
            )

    await _validate_spec_session_ownership(body.spec, user.id)

    active = await count_user_active_batches(user.id)
    if active >= settings.BATCH_MAX_CONCURRENT_PER_USER:
        raise api_error(
            422,
            "BATCH_LIMIT_EXCEEDED",
            f"You already have {active} non-terminal batches; "
            "wait for one to finish or cancel it before starting another.",
            field=None,
        )

    now = datetime.now(timezone.utc)
    spec_json = body.spec.model_dump_json()
    if len(spec_json.encode("utf-8")) > 32 * 1024:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "spec exceeds 32 KB after serialisation",
            field="spec",
        )

    batch = Batch(
        id=new_batch_id(),
        user_id=user.id,
        title=body.title,
        status=SUBMITTING,
        spec_json=spec_json,
        total_job_count=body.total_job_count,
        submitted_count=0,
        succeeded_count=0,
        failed_count=0,
        cancelled_count=0,
        last_activity_at=now,
        created_at=now,
        updated_at=now,
        finalized_at=None,
    )
    async with get_session() as session:
        session.add(batch)

    await _emit(batch, in_flight=0)

    summary = _row_to_summary(batch)
    return BatchCreateResponse(
        **summary.model_dump(),
        spec=body.spec,
    )


# ---------------------------------------------------------------------------
# GET /api/batches — list
# ---------------------------------------------------------------------------


@router.get("", response_model=BatchListResponse)
async def list_batches(
    user: CurrentUser,
    status: str = Query(default="non_terminal,recent"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> BatchListResponse:
    """Return paginated batches for the user.

    ``status`` accepts a comma-separated list of:

    - ``non_terminal`` — submitting + running.
    - ``recent`` — terminal batches finalized in the last 24 hours.
    - ``all`` — every status.
    - any individual status name.
    """
    wanted = _parse_status_filter(status)
    async with get_session() as session:
        stmt = select(Batch).where(Batch.user_id == user.id)
        if "all" not in wanted:
            recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            clauses = []
            if "non_terminal" in wanted:
                clauses.append(Batch.status.in_(tuple(NON_TERMINAL)))
            if "recent" in wanted:
                clauses.append(
                    and_(
                        Batch.status.in_(tuple(TERMINAL)),
                        Batch.finalized_at >= recent_cutoff,
                    )
                )
            for status_name in wanted - {"non_terminal", "recent", "all"}:
                clauses.append(Batch.status == status_name)
            if clauses:
                stmt = stmt.where(or_(*clauses))
            else:
                # Empty after dropping aliases — return nothing.
                return BatchListResponse(items=[], next_cursor=None)
        stmt = stmt.order_by(desc(Batch.updated_at), desc(Batch.id)).limit(
            limit + 1
        )
        if cursor is not None:
            cursor_row = (
                await session.execute(
                    select(Batch.updated_at, Batch.id).where(Batch.id == cursor)
                )
            ).one_or_none()
            if cursor_row is not None:
                cursor_updated, cursor_id = cursor_row
                stmt = stmt.where(
                    or_(
                        Batch.updated_at < cursor_updated,
                        and_(
                            Batch.updated_at == cursor_updated,
                            Batch.id < cursor_id,
                        ),
                    )
                )
        rows = (await session.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].id if has_more and page else None
    return BatchListResponse(
        items=[_row_to_summary(r) for r in page],
        next_cursor=next_cursor,
    )


def _parse_status_filter(raw: str) -> set[str]:
    parts = {p.strip() for p in raw.split(",") if p.strip()}
    if not parts:
        return {"non_terminal", "recent"}
    return parts


# ---------------------------------------------------------------------------
# GET /api/batches/<id> — detail
# ---------------------------------------------------------------------------


@router.get("/{batch_id}", response_model=BatchDetail)
async def get_batch(batch_id: str, user: CurrentUser) -> BatchDetail:
    async with get_session() as session:
        batch = await _load_owned_batch(batch_id, user.id, session)
        in_flight = await in_flight_count(batch_id, session)
        # Pull every job for the batch with its identity + status, then
        # group by set_id in Python — keeps the query simple and avoids
        # a second round-trip per slot.
        rows = (
            await session.execute(
                select(
                    Job.hash_id,
                    Job.set_id,
                    Job.status,
                    Job.seq_no,
                ).where(
                    Job.batch_id == batch_id,
                    Job.user_id == user.id,
                ).order_by(Job.seq_no.asc())
            )
        ).all()

    spec = BatchSpec.model_validate_json(batch.spec_json)
    slot_buckets: dict[int | None, dict[str, Any]] = {}
    for slot in spec.slots:
        slot_buckets[slot.stable_idx] = {
            "stable_idx": slot.stable_idx,
            "title": slot.title,
            "set_id": slot.set_id,
            "session_id": slot.session_id,
            "image_count": slot.image_count,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "in_flight": 0,
            "queued": 0,
            "job_hash_ids": [],
        }
    # Partition jobs into slots by matching set_id; jobs without a set_id
    # are aggregated under the "loose" bucket (image_count==1 single jobs).
    for hash_id, set_id, status, _seq in rows:
        slot = _find_slot_for_set_id(spec.slots, set_id)
        if slot is None:
            # Add to a synthetic slot-0 bucket so the count still
            # surfaces in the drawer even if the spec didn't account
            # for it (unlikely; defensive).
            slot_key = 0
            if slot_key not in slot_buckets:
                slot_buckets[slot_key] = {
                    "stable_idx": 0,
                    "title": "(unbound)",
                    "set_id": None,
                    "session_id": None,
                    "image_count": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "in_flight": 0,
                    "queued": 0,
                    "job_hash_ids": [],
                }
            bucket = slot_buckets[slot_key]
        else:
            bucket = slot_buckets[slot.stable_idx]
        bucket["job_hash_ids"].append(hash_id)
        if status == "SUCCEEDED":
            bucket["succeeded"] += 1
        elif status == "FAILED":
            bucket["failed"] += 1
        elif status == "CANCELLED":
            bucket["cancelled"] += 1
        elif status == "RUNNING":
            bucket["in_flight"] += 1
        elif status == "QUEUED":
            bucket["queued"] += 1

    slots_out = sorted(
        (BatchSlotProgress(**b) for b in slot_buckets.values()),
        key=lambda s: s.stable_idx,
    )

    summary = _row_to_summary(batch)
    return BatchDetail(
        **summary.model_dump(),
        spec=spec,
        in_flight_count=in_flight,
        slots=slots_out,
    )


def _find_slot_for_set_id(
    slots: list[BatchSlotSpec], set_id: str | None
) -> BatchSlotSpec | None:
    if set_id is None:
        # Fallback: a single-image slot with image_count==1 may have set_id=None.
        for s in slots:
            if s.set_id is None and s.image_count == 1:
                return s
        return None
    for s in slots:
        if s.set_id == set_id:
            return s
    return None


# ---------------------------------------------------------------------------
# POST /api/batches/<id>/finalize_submission
# ---------------------------------------------------------------------------


@router.post(
    "/{batch_id}/finalize_submission", response_model=BatchActionResponse
)
async def finalize_submission(
    batch_id: str, user: CurrentUser
) -> BatchActionResponse:
    """Move a ``submitting`` batch to its next state.

    Picks the result of :func:`resolve_terminal_status` if every Job is
    already terminal (or ``submitted_count < total_job_count`` — user
    explicitly gave up on the rest); ``running`` otherwise.
    """
    async with get_session() as session:
        batch = await _load_owned_batch(batch_id, user.id, session)
        if batch.status != SUBMITTING:
            raise api_error(
                409,
                "BATCH_INVALID_STATE",
                f"Batch in status {batch.status}; finalize only works on submitting.",
                field="status",
            )

        now = datetime.now(timezone.utc)
        in_flight = await in_flight_count(batch_id, session)
        terminal_count = (
            batch.succeeded_count + batch.failed_count + batch.cancelled_count
        )
        if (
            batch.submitted_count >= batch.total_job_count
            and terminal_count == batch.total_job_count
        ):
            batch.status = resolve_terminal_status(
                succeeded=batch.succeeded_count,
                failed=batch.failed_count,
                cancelled=batch.cancelled_count,
                total=batch.total_job_count,
            )
            batch.finalized_at = now
        elif batch.submitted_count < batch.total_job_count and in_flight == 0:
            # User skipped some Jobs and nothing is running. Resolve to a
            # terminal — partial when there's mismatch, completed only if
            # everything submitted succeeded.
            if terminal_count == batch.submitted_count:
                if batch.failed_count or batch.cancelled_count:
                    batch.status = resolve_terminal_status(
                        succeeded=batch.succeeded_count,
                        failed=batch.failed_count,
                        cancelled=batch.cancelled_count,
                        total=batch.total_job_count,
                    )
                else:
                    # Submitted all but didn't reach total → partial.
                    batch.status = "partial"
            else:
                batch.status = RUNNING
            batch.finalized_at = now if batch.status in TERMINAL else None
        else:
            batch.status = RUNNING
        batch.updated_at = now

    await _emit(batch, in_flight=in_flight)
    return BatchActionResponse(batch_id=batch.id, status=batch.status)


# ---------------------------------------------------------------------------
# POST /api/batches/<id>/cancel
# ---------------------------------------------------------------------------


@router.post("/{batch_id}/cancel", response_model=BatchActionResponse)
async def cancel_batch(batch_id: str, user: CurrentUser) -> BatchActionResponse:
    """Cancel every QUEUED Job in the batch (RUNNING is left alone)."""
    queue = get_job_queue()
    lifecycle = get_job_lifecycle()
    async with get_session() as session:
        batch = await _load_owned_batch(batch_id, user.id, session)
        if batch.status in TERMINAL:
            raise api_error(
                409,
                "BATCH_INVALID_STATE",
                f"Batch already terminal ({batch.status}).",
                field="status",
            )
        rows = (
            await session.execute(
                select(Job.hash_id).where(
                    Job.batch_id == batch_id,
                    Job.user_id == user.id,
                    Job.status == QUEUED,
                )
            )
        ).all()
        hash_ids = [r[0] for r in rows]

    cancelled_count_now = 0
    for hash_id in hash_ids:
        # Pull from the in-memory queue so the scheduler doesn't pick it
        # up between transition + the next admission tick.
        try:
            await queue.remove(hash_id)
        except Exception:  # pragma: no cover — best-effort
            logger.debug("queue.remove failed for %s; continuing", hash_id)
        try:
            await lifecycle.transition(
                hash_id, CANCELLED, reason="USER_CANCELLED_BATCH"
            )
            cancelled_count_now += 1
        except Exception:  # pragma: no cover — race with scheduler
            logger.debug(
                "lifecycle.transition CANCELLED failed for %s; skipping",
                hash_id,
            )
        # Refund the user's daily counter for queue-time cancels —
        # mirror semantics of single-job cancel.
        try:
            await get_quota_guard().refund_usage(user)
        except Exception:  # pragma: no cover
            pass

    # Re-read post-cancel to surface the latest counters; the lifecycle
    # hooks may already have flipped the row to a terminal state.
    async with get_session() as session:
        batch = await _load_owned_batch(batch_id, user.id, session)
        in_flight = await in_flight_count(batch_id, session)

    await _emit(batch, in_flight=in_flight)
    return BatchActionResponse(
        batch_id=batch.id,
        status=batch.status,
        cancelled_now_count=cancelled_count_now,
    )


# ---------------------------------------------------------------------------
# DELETE /api/batches/<id>
# ---------------------------------------------------------------------------


@router.delete("/{batch_id}", response_model=BatchActionResponse)
async def delete_batch(
    batch_id: str,
    user: CurrentUser,
    keep_jobs: bool = Query(default=True),
) -> BatchActionResponse:
    """Drop a terminal batch row.

    ``keep_jobs=true`` (the default) clears ``jobs.batch_id`` so the
    archive view is unaffected; ``keep_jobs=false`` deletes the bound
    Job rows by their hash via the standard lifecycle.
    """
    async with get_session() as session:
        batch = await _load_owned_batch(batch_id, user.id, session)
        if batch.status not in TERMINAL:
            raise api_error(
                409,
                "BATCH_INVALID_STATE",
                "Batch is still running. Cancel or wait for it to finish "
                "before deleting.",
                field="status",
            )
        status_at_delete = batch.status

        if keep_jobs:
            await session.execute(
                update(Job)
                .where(Job.batch_id == batch_id, Job.user_id == user.id)
                .values(batch_id=None)
            )
            await session.execute(delete(Batch).where(Batch.id == batch_id))
        else:
            # Pull job hash ids so we can drive the lifecycle DELETED path.
            rows = (
                await session.execute(
                    select(Job.hash_id).where(
                        Job.batch_id == batch_id,
                        Job.user_id == user.id,
                    )
                )
            ).all()
            await session.execute(delete(Batch).where(Batch.id == batch_id))
            hash_ids = [r[0] for r in rows]
            # Job lifecycle DELETE is best-effort; we don't roll back if a
            # single row fails because the parent has already been removed.
            from app.domain.job_lifecycle import DELETED, get_job_lifecycle

            for hash_id in hash_ids:
                try:
                    await get_job_lifecycle().transition(
                        hash_id, DELETED, reason="BATCH_DELETED"
                    )
                except Exception:
                    logger.debug(
                        "lifecycle DELETE failed for %s; continuing",
                        hash_id,
                    )

    return BatchActionResponse(batch_id=batch_id, status=status_at_delete)


# ---------------------------------------------------------------------------
# Re-exports — silence linter
# ---------------------------------------------------------------------------


_ = func
