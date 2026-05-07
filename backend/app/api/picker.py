"""Picker page write + read endpoints — PRD v1.

Three classes of routes:

1. **Per-image judgments** —
   ``POST /api/jobs/<hash>/images/<order>/{pick,discard,final,defer,unjudge}``.
   Each is a thin wrapper that delegates the state-machine work to
   :func:`app.services.picker_logic.transition_image` and broadcasts the
   resulting ``image_pick_state`` events to the user.

2. **Session-level Picker reads + writes** —
   ``GET  /api/sessions/<id>/picker``       — judging-page bundle
   ``PATCH /api/sessions/<id>/cursor``      — persist last-viewed image
   ``POST /api/sessions/<id>/finalize``     — lock session, broadcast SSE
   ``POST /api/sessions/<id>/unfinalize``   — re-open for judging.

3. **Deck overview** —
   ``GET /api/picker/overview``             — entry-page dashboard data.

Authorisation: every route is gated on :data:`CurrentUser` and
additionally checks the resource belongs to the caller. Cross-tenant
access returns 404 (don't leak existence).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.db.engine import get_session
from app.db.models import (
    Image,
    Job,
    Session as SessionRow,
    SessionJob,
)
from app.deps import CurrentUser
from app.domain.sse_hub import get_sse_hub
from app.schemas.picker import (
    DeckOverviewResponse,
    DeckOverviewSummary,
    DeckOverviewTotals,
    DeckSessionInFlight,
    DeckSessionStats,
    DeckSessionSummary,
    PickerImage,
    PickerJob,
    PickerSessionMeta,
    PickStateResponse,
    SessionCursorRequest,
    SessionCursorResponse,
    SessionFinalizeResponse,
    SessionPickerResponse,
)
from app.services import picker_logic
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.api.picker")

router = APIRouter(prefix="/api", tags=["picker"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VISIBLE_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")


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


def _display_name_for(model_id: str) -> str:
    from app.domain.model_catalog import _MODEL_DISPLAY  # type: ignore[attr-defined]

    info = _MODEL_DISPLAY.get(model_id)
    if not info:
        return model_id
    return str(info.get("display_name") or model_id)


async def _load_owned_image(
    hash_id: str, order: int, user_id: str
) -> tuple[Job, Image]:
    """Resolve a (job, image) pair for the caller, 404-ing on miss."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(Job, Image)
                .join(Image, Image.job_id == Job.id)
                .where(
                    Job.hash_id == hash_id,
                    Job.user_id == user_id,
                    Job.status.in_(_VISIBLE_STATUSES),
                    Image.img_order == order,
                )
            )
        ).first()
    if row is None:
        raise api_error(404, "NOT_FOUND", "Image not found.", field="hash_id")
    return row[0], row[1]


async def _broadcast_pick_events(
    user_id: str, events: list[dict[str, Any]]
) -> None:
    hub = get_sse_hub()
    for ev in events:
        try:
            await hub.broadcast_to_user(user_id, "image_pick_state", ev)
        except Exception:  # pragma: no cover — broadcast is best-effort
            logger.exception("picker: broadcast failed")


async def _broadcast_session_finalized(
    user_id: str, sess: SessionRow
) -> None:
    payload = {
        "session_id": sess.id,
        "picker_state": sess.picker_state,
        "final_image_id": sess.final_image_id,
        "ts": _aware_utc(sess.updated_at).isoformat()
        if sess.updated_at
        else None,
    }
    try:
        await get_sse_hub().broadcast_to_user(
            user_id, "session_finalized", payload
        )
    except Exception:  # pragma: no cover
        logger.exception("picker: session_finalized broadcast failed")


def _build_pick_response(
    image: Image,
    job: Job,
    sess: SessionRow | None,
    *,
    previous_final_image_id: str | None = None,
) -> PickStateResponse:
    return PickStateResponse(
        image_id=image.id,
        hash_id=job.hash_id,
        order=image.img_order,
        pick_state=image.pick_state or "unjudged",
        pick_state_updated_at=_aware_utc(image.pick_state_updated_at)
        or datetime.now(timezone.utc),
        session_id=sess.id if sess is not None else None,
        session_picker_state=(sess.picker_state if sess is not None else "not_started"),
        session_final_image_id=(
            sess.final_image_id if sess is not None else None
        ),
        previous_final_image_id=previous_final_image_id,
        starred=bool(image.starred),
    )


# ---------------------------------------------------------------------------
# Per-image judgments
# ---------------------------------------------------------------------------


async def _do_transition(
    *, hash_id: str, order: int, user_id: str, new_state: str
) -> PickStateResponse:
    async with get_session() as session:
        row = (
            await session.execute(
                select(Job, Image)
                .join(Image, Image.job_id == Job.id)
                .where(
                    Job.hash_id == hash_id,
                    Job.user_id == user_id,
                    Job.status.in_(_VISIBLE_STATUSES),
                    Image.img_order == order,
                )
            )
        ).first()
        if row is None:
            raise api_error(404, "NOT_FOUND", "Image not found.", field="hash_id")
        job, image = row[0], row[1]
        image_obj, sess, events, prev_final = await picker_logic.transition_image(
            session,
            user_id=user_id,
            job=job,
            image=image,
            new_state=new_state,
        )
    await _broadcast_pick_events(user_id, events)
    return _build_pick_response(
        image_obj, job, sess, previous_final_image_id=prev_final
    )


@router.post(
    "/jobs/{hash_id}/images/{order}/pick", response_model=PickStateResponse
)
async def post_image_pick(
    hash_id: str, order: int, user: CurrentUser
) -> PickStateResponse:
    return await _do_transition(
        hash_id=hash_id, order=order, user_id=user.id, new_state="picked"
    )


@router.post(
    "/jobs/{hash_id}/images/{order}/discard", response_model=PickStateResponse
)
async def post_image_discard(
    hash_id: str, order: int, user: CurrentUser
) -> PickStateResponse:
    return await _do_transition(
        hash_id=hash_id, order=order, user_id=user.id, new_state="discarded"
    )


@router.post(
    "/jobs/{hash_id}/images/{order}/final", response_model=PickStateResponse
)
async def post_image_final(
    hash_id: str, order: int, user: CurrentUser
) -> PickStateResponse:
    return await _do_transition(
        hash_id=hash_id, order=order, user_id=user.id, new_state="final"
    )


@router.post(
    "/jobs/{hash_id}/images/{order}/defer", response_model=PickStateResponse
)
async def post_image_defer(
    hash_id: str, order: int, user: CurrentUser
) -> PickStateResponse:
    return await _do_transition(
        hash_id=hash_id, order=order, user_id=user.id, new_state="deferred"
    )


@router.post(
    "/jobs/{hash_id}/images/{order}/unjudge", response_model=PickStateResponse
)
async def post_image_unjudge(
    hash_id: str, order: int, user: CurrentUser
) -> PickStateResponse:
    return await _do_transition(
        hash_id=hash_id, order=order, user_id=user.id, new_state="unjudged"
    )


# ---------------------------------------------------------------------------
# /api/sessions/<id>/picker — judging-page bundle
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/picker", response_model=SessionPickerResponse
)
async def get_session_picker(
    session_id: str, user: CurrentUser
) -> SessionPickerResponse:
    """Bundle every datum the picker page renders in its first paint.

    Includes the session metadata, every job (including in-flight) and
    every image with its judgment state. PRD §7.1.1 requires this be a
    single request (no N+1 round trips) — the client splits the
    response into its in-memory state shape on the way in.
    """
    async with get_session() as session:
        sess = (
            await session.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if sess is None:
            raise api_error(
                404, "NOT_FOUND", "Session not found.", field="session_id"
            )

        # All visible jobs in this session, ordered created_at ASC so the
        # frontend can compute the global image index from job_idx + order.
        job_rows = (
            await session.execute(
                select(Job)
                .join(SessionJob, SessionJob.job_id == Job.id)
                .where(
                    SessionJob.session_id == session_id,
                    Job.status.in_(_VISIBLE_STATUSES),
                )
                .order_by(Job.created_at.asc(), Job.hash_id.asc())
            )
        ).scalars().all()
        if not job_rows:
            return SessionPickerResponse(
                session=_session_meta_to_schema(sess),
                jobs=[],
                images=[],
            )

        job_ids = [j.id for j in job_rows]
        image_rows = (
            await session.execute(
                select(Image)
                .where(Image.job_id.in_(job_ids))
                .order_by(Image.job_id, Image.img_order)
            )
        ).scalars().all()

    # Build per-job index → ordering used by the picker meta-rail "job
    # #N · slot M" label.
    job_idx_for: dict[str, int] = {j.id: i for i, j in enumerate(job_rows)}
    job_by_id: dict[str, Job] = {j.id: j for j in job_rows}

    jobs_payload: list[PickerJob] = []
    for job in job_rows:
        params = _safe_load_json(job.params_json)
        prompt = str(params.pop("prompt", "") or "")
        params.pop("model", None)
        jobs_payload.append(
            PickerJob(
                hash_id=job.hash_id,
                status=job.status,
                model=job.model,
                model_display_name=_display_name_for(job.model),
                prompt=prompt,
                params=params,
                created_at=_aware_utc(job.created_at) or datetime.now(timezone.utc),
                started_at=_aware_utc(job.started_at),
                finished_at=_aware_utc(job.finished_at),
                error=job.status_reason if job.status == "FAILED" else None,
            )
        )

    images_payload: list[PickerImage] = []
    for img in image_rows:
        job = job_by_id.get(img.job_id)
        if job is None:
            continue
        params = _safe_load_json(job.params_json)
        seed = params.get("seed")
        images_payload.append(
            PickerImage(
                image_id=img.id,
                hash_id=job.hash_id,
                order=img.img_order,
                thumb_url=f"/api/jobs/{job.hash_id}/images/{img.img_order}/thumb",
                download_url=f"/api/jobs/{job.hash_id}/images/{img.img_order}/original",
                pick_state=img.pick_state or "unjudged",
                pick_state_updated_at=_aware_utc(img.pick_state_updated_at),
                starred=bool(img.starred),
                width=img.width,
                height=img.height,
                seed=str(seed) if seed is not None else None,
                job_idx=job_idx_for.get(img.job_id, 0),
            )
        )

    return SessionPickerResponse(
        session=_session_meta_to_schema(sess),
        jobs=jobs_payload,
        images=images_payload,
    )


def _session_meta_to_schema(sess: SessionRow) -> PickerSessionMeta:
    return PickerSessionMeta(
        id=sess.id,
        name=sess.name,
        picker_state=sess.picker_state or "not_started",
        final_image_id=sess.final_image_id,
        cursor_image_id=sess.cursor_image_id,
        finalized_at=_aware_utc(sess.finalized_at),
        created_at=_aware_utc(sess.created_at) or datetime.now(timezone.utc),
        updated_at=_aware_utc(sess.updated_at) or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Cursor / finalize / unfinalize
# ---------------------------------------------------------------------------


@router.patch(
    "/sessions/{session_id}/cursor", response_model=SessionCursorResponse
)
async def patch_session_cursor(
    session_id: str,
    body: SessionCursorRequest,
    user: CurrentUser,
) -> SessionCursorResponse:
    """Persist the user's last-viewed image so the picker resumes here.

    Deliberately does NOT touch ``updated_at`` — the deck-overview's
    "last edited" sort is meaningful only for actual edits, not for
    "the user looked at the page".
    """
    async with get_session() as session:
        sess = (
            await session.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if sess is None:
            raise api_error(
                404, "NOT_FOUND", "Session not found.", field="session_id"
            )
        sess.cursor_image_id = body.cursor_image_id
    return SessionCursorResponse(
        session_id=session_id, cursor_image_id=body.cursor_image_id
    )


@router.post(
    "/sessions/{session_id}/finalize", response_model=SessionFinalizeResponse
)
async def post_session_finalize(
    session_id: str, user: CurrentUser
) -> SessionFinalizeResponse:
    """Lock a session: validate preconditions, set picker_state=finalized.

    Idempotent (PRD §17.4 / §11.5): a second call on an already-
    finalized session returns 200 with the current state instead of
    409, so two-tab races don't surprise the user.
    """
    async with get_session() as session:
        sess = (
            await session.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if sess is None:
            raise api_error(
                404, "NOT_FOUND", "Session not found.", field="session_id"
            )

        # Idempotency: already finalized → reflect current truth.
        if sess.picker_state == "finalized":
            return SessionFinalizeResponse(
                session_id=session_id,
                picker_state="finalized",
                finalized_at=_aware_utc(sess.finalized_at),
                final_image_id=sess.final_image_id,
            )

        # Preconditions: every successful image is judged + a final exists.
        counts_rows = (
            await session.execute(
                select(Image.pick_state, func.count(Image.id))
                .join(Job, Job.id == Image.job_id)
                .join(SessionJob, SessionJob.job_id == Job.id)
                .where(SessionJob.session_id == session_id)
                .group_by(Image.pick_state)
            )
        ).all()
        counts = {state: int(n) for state, n in counts_rows}
        unjudged = counts.get("unjudged", 0)
        deferred = counts.get("deferred", 0)
        finals = counts.get("final", 0)
        if unjudged or deferred or finals == 0:
            raise api_error(
                409,
                "INVALID_PICKER_STATE",
                "Cannot finalize: judging is not complete.",
                field="picker_state",
                extra={
                    "unjudged": unjudged,
                    "deferred": deferred,
                    "final_image_id": sess.final_image_id,
                },
            )
        if not sess.final_image_id:
            raise api_error(
                409,
                "INVALID_PICKER_STATE",
                "Cannot finalize: no final image selected.",
                field="picker_state",
            )

        now = datetime.now(timezone.utc)
        sess.picker_state = "finalized"
        sess.finalized_at = now
        sess.updated_at = now

    await _broadcast_session_finalized(user.id, sess)
    return SessionFinalizeResponse(
        session_id=session_id,
        picker_state="finalized",
        finalized_at=_aware_utc(sess.finalized_at),
        final_image_id=sess.final_image_id,
    )


@router.post(
    "/sessions/{session_id}/unfinalize", response_model=SessionFinalizeResponse
)
async def post_session_unfinalize(
    session_id: str, user: CurrentUser
) -> SessionFinalizeResponse:
    """Re-open a finalized session for further judging.

    Keeps ``final_image_id`` so the user's original choice survives;
    the picker state simply drops back to ``judging`` (or
    ``not_started`` if there are no images at all).
    """
    async with get_session() as session:
        sess = (
            await session.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if sess is None:
            raise api_error(
                404, "NOT_FOUND", "Session not found.", field="session_id"
            )

        sess.picker_state = "judging"
        sess.finalized_at = None
        sess.updated_at = datetime.now(timezone.utc)

        # Recompute defensively — if the session is empty it should land
        # on ``not_started``.
        await picker_logic.recompute_session_picker_state(
            session, session_id=session_id, user_id=user.id
        )

    await _broadcast_session_finalized(user.id, sess)
    return SessionFinalizeResponse(
        session_id=session_id,
        picker_state=sess.picker_state,
        finalized_at=_aware_utc(sess.finalized_at),
        final_image_id=sess.final_image_id,
    )


# ---------------------------------------------------------------------------
# Deck overview
# ---------------------------------------------------------------------------


@router.get("/picker/overview", response_model=DeckOverviewResponse)
async def get_picker_overview(user: CurrentUser) -> DeckOverviewResponse:
    """Aggregate every session into the deck-overview entry-page payload.

    Single round trip per PRD §7.1.1. Returns:
    - per-state counts for each session
    - in-flight job tallies for each session
    - the final image's thumbnail URL when available
    - the most recent prompt across the session's jobs
    """
    async with get_session() as session:
        sess_rows = (
            await session.execute(
                select(SessionRow)
                .where(SessionRow.user_id == user.id)
                .order_by(SessionRow.updated_at.desc())
            )
        ).scalars().all()

        if not sess_rows:
            return DeckOverviewResponse(
                deck_title=_default_deck_title(user.id),
                last_edited=None,
                totals=DeckOverviewTotals(),
                summary=DeckOverviewSummary(),
                sessions=[],
            )

        sess_ids = [s.id for s in sess_rows]

        # Aggregate per-session image-state counts in one pass.
        pick_count_rows = (
            await session.execute(
                select(
                    SessionJob.session_id,
                    Image.pick_state,
                    func.count(Image.id),
                )
                .join(Job, Job.id == SessionJob.job_id)
                .join(Image, Image.job_id == Job.id)
                .where(SessionJob.session_id.in_(sess_ids))
                .group_by(SessionJob.session_id, Image.pick_state)
            )
        ).all()

        per_session_counts: dict[str, dict[str, int]] = {}
        for sid, state, n in pick_count_rows:
            per_session_counts.setdefault(sid, {})[state] = int(n)

        # Aggregate per-session in-flight job counts.
        job_count_rows = (
            await session.execute(
                select(
                    SessionJob.session_id,
                    Job.status,
                    func.count(Job.id),
                )
                .join(Job, Job.id == SessionJob.job_id)
                .where(
                    SessionJob.session_id.in_(sess_ids),
                    Job.status.in_(_VISIBLE_STATUSES),
                )
                .group_by(SessionJob.session_id, Job.status)
            )
        ).all()
        per_session_jobs: dict[str, dict[str, int]] = {}
        for sid, status, n in job_count_rows:
            per_session_jobs.setdefault(sid, {})[status] = int(n)

        # Find the latest prompt for each session by joining on the most
        # recent SUCCEEDED job. Cheap because we only walk the per-user
        # set we already have.
        latest_prompts: dict[str, str] = {}
        if sess_ids:
            prompt_rows = (
                await session.execute(
                    select(
                        SessionJob.session_id,
                        Job.params_json,
                        Job.created_at,
                    )
                    .join(Job, Job.id == SessionJob.job_id)
                    .where(
                        SessionJob.session_id.in_(sess_ids),
                        Job.status.in_(_VISIBLE_STATUSES),
                    )
                    .order_by(Job.created_at.desc())
                )
            ).all()
            for sid, params_json, _ in prompt_rows:
                if sid in latest_prompts:
                    continue
                params = _safe_load_json(params_json)
                p = params.get("prompt")
                if p:
                    latest_prompts[sid] = str(p)

        # Resolve final-image thumb URLs in one go.
        final_image_rows = (
            await session.execute(
                select(Image, Job)
                .join(Job, Job.id == Image.job_id)
                .where(
                    Image.id.in_(
                        [s.final_image_id for s in sess_rows if s.final_image_id]
                    )
                )
            )
        ).all() if any(s.final_image_id for s in sess_rows) else []
        final_thumb_for: dict[str, str] = {}
        for img, job in final_image_rows:
            final_thumb_for[img.id] = (
                f"/api/jobs/{job.hash_id}/images/{img.img_order}/thumb"
            )

    summaries: list[DeckSessionSummary] = []
    totals = DeckOverviewTotals()
    summary = DeckOverviewSummary()
    last_edited: datetime | None = None

    for sess in sess_rows:
        counts = per_session_counts.get(sess.id, {})
        stats = DeckSessionStats(
            final=counts.get("final", 0),
            picked=counts.get("picked", 0),
            discarded=counts.get("discarded", 0),
            deferred=counts.get("deferred", 0),
            unjudged=counts.get("unjudged", 0),
        )
        image_count = (
            stats.final + stats.picked + stats.discarded
            + stats.deferred + stats.unjudged
        )
        judged = stats.final + stats.picked + stats.discarded
        job_status_counts = per_session_jobs.get(sess.id, {})
        in_flight = DeckSessionInFlight(
            queued=job_status_counts.get("QUEUED", 0),
            running=job_status_counts.get("RUNNING", 0),
            failed=job_status_counts.get("FAILED", 0),
        )

        # ready_to_finalize = no unjudged + no deferred + has final +
        # state is judging (computed; not persisted).
        is_ready = (
            sess.picker_state == "judging"
            and stats.unjudged == 0
            and stats.deferred == 0
            and sess.final_image_id is not None
            and image_count > 0
        )

        summaries.append(
            DeckSessionSummary(
                id=sess.id,
                name=sess.name,
                picker_state=sess.picker_state or "not_started",
                image_count=image_count,
                judged_count=judged,
                stats=stats,
                in_flight=in_flight,
                final_image_id=sess.final_image_id,
                final_thumb_url=(
                    final_thumb_for.get(sess.final_image_id)
                    if sess.final_image_id
                    else None
                ),
                last_prompt=latest_prompts.get(sess.id),
                updated_at=_aware_utc(sess.updated_at) or datetime.now(timezone.utc),
                created_at=_aware_utc(sess.created_at) or datetime.now(timezone.utc),
                finalized_at=_aware_utc(sess.finalized_at),
            )
        )

        totals.images += image_count
        totals.judged += judged
        totals.inflight += (
            in_flight.queued + in_flight.running + in_flight.failed
        )

        if sess.picker_state == "finalized":
            summary.finalized += 1
        elif is_ready:
            summary.ready_to_finalize += 1
        elif sess.picker_state == "judging":
            summary.judging += 1
        else:
            summary.not_started += 1

        if sess.updated_at and (
            last_edited is None
            or _aware_utc(sess.updated_at) > last_edited
        ):
            last_edited = _aware_utc(sess.updated_at)

    return DeckOverviewResponse(
        deck_title=_default_deck_title(user.id),
        last_edited=last_edited,
        totals=totals,
        summary=summary,
        sessions=summaries,
    )


def _default_deck_title(user_id: str) -> str:
    """Return the user's deck title for the overview page header.

    PRD §8.4 says the deck title lives in user preferences as an opt-in
    field. Until that field is wired we surface a static fallback so
    the API contract stays stable.
    """
    # The user preferences module already stores per-user JSON-y
    # settings; the picker_deck_title is a future addition. For now
    # always surface "Untitled deck" so the frontend never has to
    # reason about an absent field.
    return "Untitled deck"
