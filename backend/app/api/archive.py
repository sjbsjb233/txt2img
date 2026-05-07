"""Archive sync + image read API — PR-14.

This module owns every read path the frontend's archive page touches.
Six route classes:

1. **Detail single** — ``GET /api/jobs/<hash>`` returns one
   :class:`JobDetail` for the right-side drawer.
2. **Index** — ``GET /api/jobs/index`` returns a tiny per-row metadata
   list used by the IndexedDB cache to compute deltas. ``?since=`` is
   a server-side high-water mark; ``?cursor=`` paginates within a
   batch.
3. **Detail batch** — ``POST /api/jobs/details`` fetches up to 50
   details in one call; missing ids surface as ``{not_found:true}``
   so the client can align the response with its request.
4. **State batch** — ``POST /api/jobs/states`` is an SSE-down fallback
   that returns just the liveness fields for up to 100 ids. Cheaper
   than ``details``.
5. **Image / reference reads** — three streaming endpoints that serve
   per-job files from ``data/jobs/<hash>/``. All three validate the
   ``hash_id`` shape and ownership before touching the filesystem; the
   thumbnail reads add long ``Cache-Control`` so browsers don't
   re-fetch on every grid scroll.
6. **Star toggle** — ``POST /api/jobs/<hash>/images/<order>/star``.

Authorisation contract: every route requires :data:`CurrentUser`. Any
job that doesn't belong to the caller returns 404 (not 403) — same
rationale as :func:`app.api.sessions.delete_session`: don't leak the
existence of someone else's id.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import desc, func, select

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import (
    Image,
    Job,
    JobReference,
    Session as SessionRow,
)
from app.deps import CurrentUser
from app.domain.job_queue import get_job_queue
from app.domain.runtime_configs import EmergencyConfig
from app.schemas.archive import (
    JobDetail,
    JobDetailNotFound,
    JobDetailsRequest,
    JobDetailsResponse,
    JobImageSummary,
    JobIndexEntry,
    JobIndexResponse,
    JobReferenceSummary,
    JobSessionSummary,
    JobSetSummary,
    JobStateEntry,
    JobStatesRequest,
    JobStatesResponse,
    JobTiming,
    StarRequest,
    StarResponse,
)
from app.services import image_io
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.archive")

router = APIRouter(prefix="/api", tags=["archive"])


# ---------------------------------------------------------------------------
# Constants / limits
# ---------------------------------------------------------------------------

# Index endpoint paging. The ceiling matches design doc §16.3 and protects
# against accidental scans by the client.
_INDEX_DEFAULT_LIMIT = 1000
_INDEX_MAX_LIMIT = 5000

# Browser cache window for thumbnails (30 days). Thumbnails are content-
# addressable in practice — we never overwrite ``02_thumb.webp`` for a
# given job — so an aggressive immutable cache is safe.
_THUMB_CACHE_CONTROL = "public, max-age=2592000, immutable"

# Statuses that the archive surface ever returns. ``DELETED`` is filtered
# out of every read path so a soft-deleted job vanishes from the user's
# view; admin still sees it through admin endpoints.
_VISIBLE_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")


# ---------------------------------------------------------------------------
# /api/jobs/index
#
# Declared before ``GET /api/jobs/{hash_id}`` so Starlette's first-match
# wins routing matches the literal "index" path against this handler
# rather than treating it as a hash_id pattern. Static paths must come
# first when they share a prefix with a dynamic one.
# ---------------------------------------------------------------------------


@router.get("/jobs/index", response_model=JobIndexResponse)
async def get_jobs_index(
    user: CurrentUser,
    since: datetime | None = Query(default=None),
    limit: int = Query(default=_INDEX_DEFAULT_LIMIT, ge=1, le=_INDEX_MAX_LIMIT),
    cursor: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
) -> JobIndexResponse:
    """Return ``hash_id`` + status + ``updated_at`` for delta sync.

    Ordering: ``updated_at DESC, hash_id ASC``. The secondary sort on
    ``hash_id`` is a stable tiebreaker so the cursor logic doesn't
    skip rows when two jobs share an updated_at second (SQLite stores
    timestamps at second precision — collisions on burst-create are
    common).

    ``since`` filters to rows whose ``updated_at >= since``. The
    client typically passes the maximum ``updated_at`` it has cached
    locally so the response carries only delta rows. Missing → full
    list (capped by ``limit``).

    ``cursor`` is the ``hash_id`` of the last row of the previous page.
    The next page returns rows whose (updated_at, hash_id) sorts
    strictly *after* the cursor row.

    ``session_id`` is an optional filter so the archive can render
    "only this session" without pulling everything client-side and
    filtering.
    """
    async with get_session() as session:
        stmt = (
            select(
                Job.hash_id,
                Job.set_id,
                Job.seq_no,
                Job.status,
                Job.updated_at,
            )
            .where(
                Job.user_id == user.id,
                Job.status.in_(_VISIBLE_STATUSES),
            )
            .order_by(desc(Job.updated_at), Job.hash_id)
            .limit(limit + 1)  # +1 so we know if there's another page
        )

        if since is not None:
            stmt = stmt.where(Job.updated_at >= since)
        if session_id is not None:
            stmt = stmt.where(Job.session_id == session_id)
        if cursor is not None:
            cursor_row = await _resolve_cursor(session, cursor, user.id)
            if cursor_row is not None:
                cur_updated, cur_hash = cursor_row
                # Strictly after the cursor: either older updated_at, or
                # equal updated_at with a hash_id that sorts later.
                stmt = stmt.where(
                    (Job.updated_at < cur_updated)
                    | (
                        (Job.updated_at == cur_updated)
                        & (Job.hash_id > cur_hash)
                    )
                )

        rows = (await session.execute(stmt)).all()

    has_more = len(rows) > limit
    page = rows[:limit]

    items = [
        JobIndexEntry(
            hash_id=hash_id,
            set_id=set_id,
            seq_no=int(seq_no),
            status=status,
            updated_at=_aware_utc(updated_at),
        )
        for hash_id, set_id, seq_no, status, updated_at in page
    ]
    next_cursor = items[-1].hash_id if has_more and items else None
    return JobIndexResponse(items=items, next_cursor=next_cursor)


async def _resolve_cursor(
    session, cursor_hash: str, user_id: str
) -> tuple[datetime, str] | None:
    """Look up the (updated_at, hash_id) of the cursor row.

    We require the cursor row to belong to the caller; a foreign cursor
    silently restarts pagination from the top so a leaked id can't
    seek into someone else's archive.
    """
    row = (
        await session.execute(
            select(Job.updated_at, Job.hash_id).where(
                Job.hash_id == cursor_hash,
                Job.user_id == user_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    updated_at, hash_id = row
    return _aware_utc(updated_at), hash_id


# ---------------------------------------------------------------------------
# /api/jobs/details (batch)
# ---------------------------------------------------------------------------


@router.post("/jobs/details", response_model=JobDetailsResponse)
async def post_jobs_details(
    body: JobDetailsRequest,
    user: CurrentUser,
) -> JobDetailsResponse:
    """Fetch full :class:`JobDetail` for up to 50 ids.

    Response order matches the request order. Missing ids surface as
    ``{hash_id, not_found:true}`` so the client can match request to
    response by index — important for refilling the cache after a
    rolling cleanup.
    """
    requested = list(body.hash_ids)
    detail_by_hash = await _load_many_details(requested, user.id)

    items: list[dict[str, Any]] = []
    for hash_id in requested:
        detail = detail_by_hash.get(hash_id)
        if detail is None:
            items.append(
                JobDetailNotFound(hash_id=hash_id).model_dump(mode="json")
            )
        else:
            items.append(detail.model_dump(mode="json"))
    return JobDetailsResponse(items=items)


# ---------------------------------------------------------------------------
# /api/jobs/states (batch, lean)
# ---------------------------------------------------------------------------


@router.post("/jobs/states", response_model=JobStatesResponse)
async def post_jobs_states(
    body: JobStatesRequest,
    user: CurrentUser,
) -> JobStatesResponse:
    """Lean batch fallback for ``status / position / ETA / updated_at``.

    Used when the SSE channel is unavailable; the client polls this to
    keep its in-memory view of QUEUED/RUNNING jobs current. Position
    + ETA are populated only for QUEUED entries (RUNNING jobs no longer
    have a queue position).
    """
    requested = list(body.hash_ids)
    if not requested:
        return JobStatesResponse(items=[])

    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    Job.hash_id,
                    Job.status,
                    Job.updated_at,
                ).where(
                    Job.user_id == user.id,
                    Job.hash_id.in_(requested),
                )
            )
        ).all()

    by_hash = {hash_id: (status, updated_at) for hash_id, status, updated_at in rows}

    queue = get_job_queue()
    items: list[JobStateEntry] = []
    for hash_id in requested:
        if hash_id not in by_hash:
            items.append(JobStateEntry(hash_id=hash_id, not_found=True))
            continue
        status, updated_at = by_hash[hash_id]
        position = None
        eta = None
        if status == "QUEUED":
            try:
                position = await queue.position(hash_id)
            except Exception:  # pragma: no cover — defensive
                position = None
            eta = _estimate_wait_seconds(position)
        items.append(
            JobStateEntry(
                hash_id=hash_id,
                status=status,
                updated_at=_aware_utc(updated_at),
                position=position,
                estimated_wait_seconds=eta,
            )
        )
    return JobStatesResponse(items=items)


# ---------------------------------------------------------------------------
# /api/jobs/<hash> — single detail
#
# Declared after every static ``/jobs/<verb>`` GET above (currently
# only ``/jobs/index``) and before the deeper ``/jobs/<hash>/images/...``
# routes. Starlette will still match those deeper paths first because
# their patterns are more specific.
# ---------------------------------------------------------------------------


@router.get("/jobs/{hash_id}", response_model=JobDetail)
async def get_job_detail(hash_id: str, user: CurrentUser) -> JobDetail:
    """Return one job's full user-visible detail.

    Cross-tenant access returns 404 (not 403). DELETED rows are
    surfaced as 404 too — the user softly-deleted them and shouldn't
    keep seeing them in their own archive.
    """
    detail = await _load_one_detail(hash_id, user.id)
    if detail is None:
        raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")
    return detail


# ---------------------------------------------------------------------------
# Image / reference file reads
# ---------------------------------------------------------------------------


@router.get("/jobs/{hash_id}/images/{order}/thumb")
async def get_image_thumb(
    hash_id: str,
    order: int,
    user: CurrentUser,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    """Stream the thumbnail webp for one output image.

    Long-cache: 30d immutable. ETag is a stable hash of the file path +
    mtime so clients can revalidate cheaply with ``If-None-Match``.
    """
    _check_image_access_allowed(user)
    job = await _load_owned_job(
        hash_id, user.id, admin_bypass=user.role == "admin"
    )
    img = await _load_image(job.id, order)

    abs_path = _resolve_under_data_root(img.thumb_path)
    return _serve_static_file(
        abs_path,
        media_type="image/webp",
        cache_control=_THUMB_CACHE_CONTROL,
        if_none_match=if_none_match,
        download_filename=None,
    )


@router.get("/jobs/{hash_id}/images/{order}/original")
async def get_image_original(
    hash_id: str,
    order: int,
    user: CurrentUser,
) -> FileResponse:
    """Stream the original output image as an attachment.

    No browser cache header — the user has clicked Download, and we
    want a re-click to prompt a fresh save dialog. The body streams
    via :class:`FileResponse` so a 50 MB original doesn't get buffered
    into memory.
    """
    _check_image_access_allowed(user)
    job = await _load_owned_job(
        hash_id, user.id, admin_bypass=user.role == "admin"
    )
    img = await _load_image(job.id, order)

    abs_path = _resolve_under_data_root(img.original_path)
    if not abs_path.exists():
        raise api_error(404, "NOT_FOUND", "Image file is no longer on the server.")

    fmt = (img.format or "").lower()
    media = (
        "image/png"
        if fmt == "png"
        else "image/jpeg"
        if fmt in ("jpg", "jpeg")
        else "image/webp"
        if fmt == "webp"
        else "application/octet-stream"
    )
    download_name = f"{hash_id}_{order:02d}.{fmt or 'bin'}"
    return FileResponse(
        abs_path,
        media_type=media,
        filename=download_name,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/jobs/{hash_id}/refs/{order}/thumb")
async def get_reference_thumb(
    hash_id: str,
    order: int,
    user: CurrentUser,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    """Stream a reference-image thumbnail.

    References don't get a separate thumb on disk — we stream the
    original (always small enough that it doesn't matter; the
    24-MB-cap on uploads applies). The route exists so the frontend
    can use the same shape as output thumbnails.
    """
    _check_image_access_allowed(user)
    job = await _load_owned_job(
        hash_id, user.id, admin_bypass=user.role == "admin"
    )
    ref = await _load_reference(job.id, order)

    abs_path = _resolve_under_data_root(ref.rel_path)
    media = (ref.mime or "").lower() or "application/octet-stream"
    return _serve_static_file(
        abs_path,
        media_type=media,
        cache_control=_THUMB_CACHE_CONTROL,
        if_none_match=if_none_match,
        download_filename=None,
    )


# ---------------------------------------------------------------------------
# Star toggle
# ---------------------------------------------------------------------------


@router.post("/jobs/{hash_id}/images/{order}/star", response_model=StarResponse)
async def post_image_star(
    hash_id: str,
    order: int,
    user: CurrentUser,
    body: StarRequest | None = None,
) -> StarResponse:
    """Toggle the ``starred`` bit on one output image.

    Body is optional; when ``starred`` is set explicitly that value
    wins, otherwise we flip the current bit. We stream the new value
    back so the client doesn't need to re-pull details to confirm.

    **Picker sync** (PRD §8.5.3): the legacy star bit also drives the
    picker's ``pick_state`` field. When the user stars an unjudged
    image, that's effectively a "pick" — promote it. When they unstar
    a picked/final, demote to unjudged (and clear final). The reverse
    propagation (picker pick → starred=true) is wired in
    :mod:`app.api.picker`.
    """
    import asyncio
    import json
    from datetime import datetime, timezone

    from app.db.models import Session as SessionRow

    job = await _load_owned_job(hash_id, user.id)

    pick_change: tuple[str, str, str | None, str | None, str | None] | None = None

    async with get_session() as session:
        img = (
            await session.execute(
                select(Image).where(
                    Image.job_id == job.id, Image.img_order == order
                )
            )
        ).scalar_one_or_none()
        if img is None:
            raise api_error(404, "NOT_FOUND", "Image not found.", field="order")

        current = bool(img.starred)
        if body and body.starred is not None:
            new_value = bool(body.starred)
        else:
            new_value = not current
        img.starred = 1 if new_value else 0

        # Mirror into pick_state so the picker page reflects the change.
        old_pick_state = img.pick_state or "unjudged"
        new_pick_state = old_pick_state
        sess_row = None
        if job.session_id:
            sess_row = (
                await session.execute(
                    select(SessionRow).where(
                        SessionRow.id == job.session_id,
                        SessionRow.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()

        if new_value and old_pick_state == "unjudged":
            new_pick_state = "picked"
        elif not new_value and old_pick_state in ("picked", "final"):
            new_pick_state = "unjudged"
            if (
                sess_row is not None
                and sess_row.final_image_id == img.id
            ):
                sess_row.final_image_id = None

        if new_pick_state != old_pick_state:
            img.pick_state = new_pick_state
            img.pick_state_updated_at = datetime.now(timezone.utc)

            # Recompute session.picker_state if it's not finalized.
            if sess_row is not None and sess_row.picker_state != "finalized":
                # Simple per-session count: any judged → judging,
                # else not_started.
                from app.db.models import SessionJob

                rows = (
                    await session.execute(
                        select(Image.pick_state)
                        .join(Job, Image.job_id == Job.id)
                        .join(SessionJob, SessionJob.job_id == Job.id)
                        .where(
                            SessionJob.session_id == sess_row.id,
                            Job.status == "SUCCEEDED",
                        )
                    )
                ).all()
                has_judged = any(
                    s and s != "unjudged" for (s,) in rows
                )
                sess_row.picker_state = "judging" if has_judged else "not_started"
                sess_row.updated_at = datetime.now(timezone.utc)

            pick_change = (
                img.id,
                old_pick_state,
                new_pick_state,
                sess_row.id if sess_row else None,
                sess_row.picker_state if sess_row else None,
            )

    # Best-effort: broadcast pick_state change so picker tabs update.
    if pick_change is not None:
        from app.domain.sse_hub import get_sse_hub

        image_id, from_state, to_state, sess_id, sess_picker_state = pick_change
        payload = {
            "image_id": image_id,
            "hash_id": hash_id,
            "order": order,
            "session_id": sess_id,
            "from": from_state,
            "to": to_state,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "session_picker_state": sess_picker_state,
            "session_final_image_id": None,
            "starred": new_value,
        }
        try:
            asyncio.create_task(
                get_sse_hub().broadcast_to_user(
                    user.id, "image_pick_state", payload
                )
            )
        except Exception:  # pragma: no cover
            logger.exception("starred->pick_state broadcast failed")

    return StarResponse(hash_id=hash_id, order=order, starred=new_value)


# ---------------------------------------------------------------------------
# Internal: loaders + projections
# ---------------------------------------------------------------------------


async def _load_owned_job(
    hash_id: str, user_id: str, *, admin_bypass: bool = False
) -> Job:
    """Fetch a job row owned by ``user_id`` or 404. Filters out DELETED.

    When ``admin_bypass`` is set the ownership predicate is dropped so
    admin-tooling routes (e.g. the inline JobInspector under the admin
    user drawer) can stream images for any user's job.
    """
    async with get_session() as session:
        q = select(Job).where(
            Job.hash_id == hash_id,
            Job.status.in_(_VISIBLE_STATUSES),
        )
        if not admin_bypass:
            q = q.where(Job.user_id == user_id)
        row = (await session.execute(q)).scalar_one_or_none()
    if row is None:
        raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")
    return row


async def _load_image(job_id: str, order: int) -> Image:
    async with get_session() as session:
        img = (
            await session.execute(
                select(Image).where(
                    Image.job_id == job_id, Image.img_order == order
                )
            )
        ).scalar_one_or_none()
    if img is None:
        raise api_error(404, "NOT_FOUND", "Image not found.", field="order")
    return img


async def _load_reference(job_id: str, order: int) -> JobReference:
    async with get_session() as session:
        ref = (
            await session.execute(
                select(JobReference).where(
                    JobReference.job_id == job_id,
                    JobReference.ref_order == order,
                )
            )
        ).scalar_one_or_none()
    if ref is None:
        raise api_error(404, "NOT_FOUND", "Reference not found.", field="order")
    return ref


async def _load_one_detail(
    hash_id: str, user_id: str
) -> JobDetail | None:
    """Build one :class:`JobDetail`, joining images, refs, session.

    Returns ``None`` when the job is missing or belongs to another
    user. Routed callers raise 404 on ``None``.
    """
    async with get_session() as session:
        job = (
            await session.execute(
                select(Job).where(
                    Job.hash_id == hash_id,
                    Job.user_id == user_id,
                    Job.status.in_(_VISIBLE_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if job is None:
            return None

        images = (
            await session.execute(
                select(Image)
                .where(Image.job_id == job.id)
                .order_by(Image.img_order)
            )
        ).scalars().all()

        refs = (
            await session.execute(
                select(JobReference)
                .where(JobReference.job_id == job.id)
                .order_by(JobReference.ref_order)
            )
        ).scalars().all()

        session_row = None
        if job.session_id is not None:
            session_row = (
                await session.execute(
                    select(SessionRow).where(
                        SessionRow.id == job.session_id,
                        SessionRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()

        set_summary = await _build_set_summary(session, job)

    return _project_detail(
        job, images, refs, session_row, set_summary
    )


async def _load_many_details(
    hash_ids: list[str], user_id: str
) -> dict[str, JobDetail]:
    """Batched detail fetch.

    Implemented as one query per join (jobs, images, references,
    sessions) and a single ``GROUP BY set_id`` for set sizes — so the
    cost stays roughly linear in the response size. The route caller
    aligns the result with the request order; we just return a dict.
    """
    if not hash_ids:
        return {}

    async with get_session() as session:
        jobs = (
            await session.execute(
                select(Job).where(
                    Job.user_id == user_id,
                    Job.hash_id.in_(hash_ids),
                    Job.status.in_(_VISIBLE_STATUSES),
                )
            )
        ).scalars().all()
        if not jobs:
            return {}

        job_ids = [j.id for j in jobs]

        images = (
            await session.execute(
                select(Image)
                .where(Image.job_id.in_(job_ids))
                .order_by(Image.job_id, Image.img_order)
            )
        ).scalars().all()

        refs = (
            await session.execute(
                select(JobReference)
                .where(JobReference.job_id.in_(job_ids))
                .order_by(JobReference.job_id, JobReference.ref_order)
            )
        ).scalars().all()

        session_ids = {j.session_id for j in jobs if j.session_id}
        session_rows: dict[str, SessionRow] = {}
        if session_ids:
            session_rows = {
                row.id: row
                for row in (
                    await session.execute(
                        select(SessionRow).where(
                            SessionRow.id.in_(session_ids),
                            SessionRow.user_id == user_id,
                        )
                    )
                ).scalars().all()
            }

        set_ids = {j.set_id for j in jobs if j.set_id}
        set_image_counts: dict[str, int] = {}
        if set_ids:
            count_rows = (
                await session.execute(
                    select(Job.set_id, func.count(Image.id))
                    .join(Image, Image.job_id == Job.id)
                    .where(
                        Job.set_id.in_(set_ids),
                        Job.user_id == user_id,
                    )
                    .group_by(Job.set_id)
                )
            ).all()
            set_image_counts = {sid: int(c) for sid, c in count_rows}

    images_by_job: dict[str, list[Image]] = {}
    for img in images:
        images_by_job.setdefault(img.job_id, []).append(img)
    refs_by_job: dict[str, list[JobReference]] = {}
    for ref in refs:
        refs_by_job.setdefault(ref.job_id, []).append(ref)

    out: dict[str, JobDetail] = {}
    for job in jobs:
        set_summary = None
        if job.set_id:
            set_summary = JobSetSummary(
                set_id=job.set_id,
                image_count=set_image_counts.get(job.set_id, 0),
            )
        session_row = (
            session_rows.get(job.session_id)
            if job.session_id is not None
            else None
        )
        out[job.hash_id] = _project_detail(
            job,
            images_by_job.get(job.id, []),
            refs_by_job.get(job.id, []),
            session_row,
            set_summary,
        )
    return out


async def _build_set_summary(session, job: Job) -> JobSetSummary | None:
    """Count the images that belong to a job's Set, if any.

    A Set's image count is the number of ``images`` rows joined
    through ``jobs.set_id`` — not the original ``n`` parameter,
    because partial failures are expected and we want the count to
    reflect what actually rendered.
    """
    if job.set_id is None:
        return None
    count = (
        await session.execute(
            select(func.count(Image.id))
            .select_from(Job)
            .join(Image, Image.job_id == Job.id)
            .where(Job.set_id == job.set_id, Job.user_id == job.user_id)
        )
    ).scalar_one()
    return JobSetSummary(set_id=job.set_id, image_count=int(count or 0))


def _project_detail(
    job: Job,
    images: list[Image],
    refs: list[JobReference],
    session_row: SessionRow | None,
    set_summary: JobSetSummary | None,
) -> JobDetail:
    params = _safe_load_json(job.params_json)
    flags = _safe_load_json(job.flags_json)

    # The params stash includes prompt/model duplicates (see PR-13's
    # create-path). Hide them from the API surface — model/prompt are
    # already top-level columns and the wire shape is cleaner without
    # the duplicates.
    params_for_wire = {k: v for k, v in params.items() if k not in ("prompt", "model")}

    timing = _build_timing(job)

    image_summaries = [
        JobImageSummary(
            image_id=img.id,
            order=img.img_order,
            thumb_url=f"/api/jobs/{job.hash_id}/images/{img.img_order}/thumb",
            download_url=f"/api/jobs/{job.hash_id}/images/{img.img_order}/original",
            width=img.width,
            height=img.height,
            format=img.format,
            file_size_bytes=img.file_size_bytes,
            starred=bool(img.starred),
        )
        for img in images
    ]

    ref_summaries = [
        JobReferenceSummary(
            order=ref.ref_order,
            filename=ref.filename,
            thumb_url=f"/api/jobs/{job.hash_id}/refs/{ref.ref_order}/thumb",
        )
        for ref in refs
    ]

    session_summary = None
    if session_row is not None:
        session_summary = JobSessionSummary(
            id=session_row.id, name=session_row.name
        )

    return JobDetail(
        hash_id=job.hash_id,
        seq_no=int(job.seq_no),
        status=job.status,
        status_reason=job.status_reason,
        model=job.model,
        model_display_name=_display_name_for(job.model),
        updated_at=_aware_utc(job.updated_at),
        set_id=job.set_id,
        session_id=job.session_id,
        prompt=str(params.get("prompt") or ""),
        params=params_for_wire,
        references=ref_summaries,
        set=set_summary,
        images=image_summaries,
        session=session_summary,
        timing=timing,
        error=job.status_reason if job.status == "FAILED" else None,
        flags=flags,
    )


def _build_timing(job: Job) -> JobTiming:
    queued = _aware_utc(job.queued_at) if job.queued_at else None
    started = _aware_utc(job.started_at) if job.started_at else None
    finished = _aware_utc(job.finished_at) if job.finished_at else None

    queue_seconds: float | None = None
    render_seconds: float | None = None

    # Queue seconds = dispatch - queue. Use ``dispatched_at`` when we
    # have it (the executor sets it before flipping to RUNNING) and
    # fall back to ``started_at`` so partial state is still informative.
    dispatched = (
        _aware_utc(job.dispatched_at) if job.dispatched_at else started
    )
    if queued is not None and dispatched is not None:
        queue_seconds = max(0.0, (dispatched - queued).total_seconds())
    if started is not None and finished is not None:
        render_seconds = max(0.0, (finished - started).total_seconds())

    return JobTiming(
        queued_at=queued,
        started_at=started,
        finished_at=finished,
        queue_seconds=queue_seconds,
        render_seconds=render_seconds,
    )


def _display_name_for(model_id: str) -> str:
    """Look up the human-friendly model name.

    Imported lazily to avoid a circular import — model_catalog imports
    schemas which sit beside this module.
    """
    from app.domain.model_catalog import _MODEL_DISPLAY  # type: ignore[attr-defined]

    info = _MODEL_DISPLAY.get(model_id)
    if not info:
        return model_id
    return str(info.get("display_name") or model_id)


# ---------------------------------------------------------------------------
# Static-file helpers
# ---------------------------------------------------------------------------


def _check_image_access_allowed(user) -> None:
    """Reject image streams while ``emergency.pause_image_access`` is on.

    Admins bypass — they need to inspect job output during an incident.
    Everyone else gets 403 ``BLOCKED_BY_EMERGENCY``. The check is here
    rather than in a FastAPI dependency because the four image-serving
    routes share enough surface to centralise the guard, but other
    archive routes (index, details, states) deliberately stay
    accessible during a pause so the UI doesn't dead-end.
    """
    if user.role == "admin":
        return
    if EmergencyConfig().pause_image_access:
        raise api_error(
            403,
            "BLOCKED_BY_EMERGENCY",
            "Image access temporarily paused by admin.",
        )


def _resolve_under_data_root(rel_path: str) -> Path:
    """Resolve ``rel_path`` against ``DATA_ROOT`` and refuse escapes.

    Never trust the stored path: we re-check that the resolved path
    sits under ``DATA_ROOT``. A future bug that lets a relative path
    contain ``..`` cannot escape the data dir thanks to this guard.
    """
    settings = get_settings()
    data_root = Path(settings.DATA_ROOT).resolve()
    if not rel_path or os.path.isabs(rel_path):
        raise api_error(404, "NOT_FOUND", "File not found.")
    candidate = (data_root / rel_path).resolve()
    if not candidate.is_relative_to(data_root):
        raise api_error(404, "NOT_FOUND", "File not found.")
    return candidate


def _serve_static_file(
    path: Path,
    *,
    media_type: str,
    cache_control: str,
    if_none_match: str | None,
    download_filename: str | None,
) -> Response:
    """Common path for thumbnail-style reads.

    Builds an ETag from ``"<size>-<mtime_ns>"`` so a downstream
    revalidate (``If-None-Match``) returns 304 without re-streaming
    the bytes.
    """
    if not path.exists():
        # Different shape than the JSON 404 elsewhere — these endpoints
        # are returning binary in the success case, so we keep the
        # JSON envelope here for consistency with the rest of the API.
        raise api_error(404, "NOT_FOUND", "File not found.")
    try:
        stat = path.stat()
    except OSError:
        raise api_error(404, "NOT_FOUND", "File not found.")

    etag = f'W/"{stat.st_size}-{stat.st_mtime_ns}"'
    if if_none_match is not None and if_none_match.strip() == etag:
        return Response(status_code=304, headers={"ETag": etag})

    headers = {
        "Cache-Control": cache_control,
        "ETag": etag,
    }
    if download_filename:
        headers["Content-Disposition"] = (
            f'attachment; filename="{download_filename}"'
        )

    return FileResponse(path, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Time / json helpers
# ---------------------------------------------------------------------------


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _estimate_wait_seconds(position: int | None) -> int | None:
    """Mirror ``app.api.jobs._estimate_wait_seconds`` so SSE-down fallback
    returns the same shape clients see at create time."""
    if position is None:
        return None
    return max(0, int(position * 12))


# Catch a Request import that is reserved for future use (e.g. when we
# want client-side fingerprinting on download). Silences unused-import
# checks without removing the import — keeps the module symmetric with
# ``api/jobs.py``.
_ = Request
