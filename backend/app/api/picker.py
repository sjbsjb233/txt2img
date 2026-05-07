"""Picker page endpoints (PRD §9).

The picker surface owns three families of routes:

1. **Per-image judgment writes** — five mirrors of the same shape that
   transition a single image into one of unjudged/picked/discarded/
   final/deferred. Each one:
     * recomputes ``session.picker_state`` from the resulting counts
     * keeps ``session.final_image_id`` consistent with the new state
     * mirrors picked/final into the legacy ``starred`` bit so the
       Archive page's star icon stays in sync (PRD §8.5)
     * broadcasts ``image_pick_state`` over SSE.

2. **Session-level operations** — finalize / unfinalize / cursor patch +
   the aggregate read ``GET /api/sessions/<id>/picker``.

3. **Deck-wide reads** — ``GET /api/picker/overview`` aggregates every
   session's stats + final-image thumb URL into one payload.

The deck-export and per-session export routes return a download token
that the existing image-streaming machinery can serve.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select, update

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
    CursorPatchRequest,
    CursorPatchResponse,
    FinalizeResponse,
    JudgmentResponse,
    PickState,
    PickerImageView,
    PickerInFlight,
    PickerJobView,
    PickerOverviewResponse,
    PickerOverviewSession,
    PickerOverviewSummary,
    PickerOverviewTotals,
    PickerSessionResponse,
    PickerSessionStats,
    PickerSessionView,
    VarySeedRequest,
)
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.picker")

# Two routers because the URL prefix differs.
images_router = APIRouter(prefix="/api/jobs", tags=["picker"])
sessions_router = APIRouter(prefix="/api/sessions", tags=["picker"])
overview_router = APIRouter(prefix="/api/picker", tags=["picker"])
vary_router = APIRouter(prefix="/api/jobs", tags=["picker"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


VISIBLE_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")
SUCCEEDED = "SUCCEEDED"
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELLED")


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
    *, session, hash_id: str, order: int, user_id: str
) -> tuple[Image, Job, SessionRow | None]:
    """Resolve image + owning job + bound session, all owned by user.

    Returns the Image row, its Job row, and the linked SessionRow (or
    ``None`` if the job isn't bound to a session). 404 on any miss.
    """
    job_row = (
        await session.execute(
            select(Job).where(
                Job.hash_id == hash_id,
                Job.user_id == user_id,
                Job.status.in_(VISIBLE_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if job_row is None:
        raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")

    img = (
        await session.execute(
            select(Image).where(
                Image.job_id == job_row.id, Image.img_order == order
            )
        )
    ).scalar_one_or_none()
    if img is None:
        raise api_error(404, "NOT_FOUND", "Image not found.", field="order")

    sess_row = None
    if job_row.session_id:
        sess_row = (
            await session.execute(
                select(SessionRow).where(
                    SessionRow.id == job_row.session_id,
                    SessionRow.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    return img, job_row, sess_row


async def _recompute_session_state(session, sess_row: SessionRow) -> None:
    """Recompute ``picker_state`` for ``sess_row`` from current image counts.

    ``ready_to_finalize`` is **not** persisted (it's a front-end-computed
    superset of ``judging``). The persisted value is one of
    ``not_started`` / ``judging`` / ``finalized``.

    If the row is currently ``finalized``, this function is a no-op —
    finalization is a user-driven flag we don't undo automatically.
    Exception: when a new image arrives in a previously-finalized
    session, callers explicitly invoke this with ``force_revert=True``
    to roll back to ``judging``. The flag is implemented by the caller
    overwriting ``picker_state`` to ``judging`` before calling this
    function. We don't add a parameter here because the heuristic is
    subtle and the call sites do their own bookkeeping anyway.
    """
    if sess_row.picker_state == "finalized":
        # Caller is responsible for un-finalizing if needed.
        return

    # Count images in this session by pick_state. We only count SUCCEEDED
    # job images because in-flight jobs don't yet have rows (or have
    # placeholders that aren't user-visible).
    rows = (
        await session.execute(
            select(Image.pick_state)
            .join(Job, Image.job_id == Job.id)
            .join(SessionJob, SessionJob.job_id == Job.id)
            .where(
                SessionJob.session_id == sess_row.id,
                Job.status == SUCCEEDED,
            )
        )
    ).all()

    if not rows:
        sess_row.picker_state = "not_started"
        return

    has_judged = False
    for (state,) in rows:
        if state and state != "unjudged":
            has_judged = True
            break

    sess_row.picker_state = "judging" if has_judged else "not_started"


async def _broadcast_pick_state(
    *,
    user_id: str,
    image_id: str,
    hash_id: str,
    order: int,
    session_id: str | None,
    from_state: str,
    to_state: str,
    session_picker_state: str | None,
    session_final_image_id: str | None,
    starred: bool,
) -> None:
    payload = {
        "image_id": image_id,
        "hash_id": hash_id,
        "order": order,
        "session_id": session_id,
        "from": from_state,
        "to": to_state,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_picker_state": session_picker_state,
        "session_final_image_id": session_final_image_id,
        "starred": starred,
    }
    try:
        await get_sse_hub().broadcast_to_user(user_id, "image_pick_state", payload)
    except Exception:  # pragma: no cover — best effort
        logger.exception("image_pick_state broadcast failed for %s", image_id)


async def _broadcast_session_finalized(
    *, user_id: str, session_id: str, picker_state: str, final_image_id: str | None
) -> None:
    payload = {
        "session_id": session_id,
        "picker_state": picker_state,
        "final_image_id": final_image_id,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        await get_sse_hub().broadcast_to_user(user_id, "session_finalized", payload)
    except Exception:  # pragma: no cover — best effort
        logger.exception("session_finalized broadcast failed for %s", session_id)


# ---------------------------------------------------------------------------
# Generic transition impl shared by the five image-write routes.
# ---------------------------------------------------------------------------


async def _set_pick_state(
    *,
    hash_id: str,
    order: int,
    user_id: str,
    target: PickState,
) -> JudgmentResponse:
    previous_final_image_id: str | None = None
    # Demotion broadcast info captured inside the transaction and
    # emitted *after* commit so other tabs never observe a state that
    # hasn't been persisted yet.
    demotion_broadcast: dict[str, Any] | None = None

    async with get_session() as session:
        img, job_row, sess_row = await _load_owned_image(
            session=session, hash_id=hash_id, order=order, user_id=user_id
        )

        from_state = img.pick_state
        if from_state == target:
            # No-op transitions return the current state without a SSE
            # broadcast. The frontend's optimistic UI doesn't need any
            # confirmation path here — same state in / out.
            return JudgmentResponse(
                image_id=img.id,
                hash_id=hash_id,
                order=order,
                pick_state=target,
                pick_state_updated_at=_aware_utc(img.pick_state_updated_at)
                or datetime.now(timezone.utc),
                starred=bool(img.starred),
                session_id=sess_row.id if sess_row else None,
                session_picker_state=(
                    sess_row.picker_state if sess_row else None
                ),
                session_final_image_id=(
                    sess_row.final_image_id if sess_row else None
                ),
            )

        # When promoting to 'final', demote any currently-final image in
        # the same session to 'picked' atomically.
        if target == "final" and sess_row is not None:
            cur_final_id = sess_row.final_image_id
            if cur_final_id and cur_final_id != img.id:
                old_final = (
                    await session.execute(
                        select(Image).where(Image.id == cur_final_id)
                    )
                ).scalar_one_or_none()
                if old_final is not None:
                    previous_final_image_id = old_final.id
                    old_from_state = old_final.pick_state
                    old_final.pick_state = "picked"
                    old_final.pick_state_updated_at = datetime.now(timezone.utc)
                    old_final.starred = 1
                    # Resolve the demoted image's owning hash_id rather
                    # than sending an empty string (Copilot review on
                    # PR #88) — downstream consumers use hash_id+order
                    # to build URLs and route audit logs.
                    if old_final.job_id == job_row.id:
                        demoted_hash_id = hash_id
                    else:
                        owner_job = (
                            await session.execute(
                                select(Job).where(Job.id == old_final.job_id)
                            )
                        ).scalar_one_or_none()
                        demoted_hash_id = (
                            owner_job.hash_id if owner_job is not None else ""
                        )
                    # Capture for post-commit dispatch. We deliberately
                    # do NOT schedule the SSE here — the context
                    # manager hasn't committed yet, and another tab
                    # could observe a state that's about to roll back.
                    demotion_broadcast = {
                        "user_id": user_id,
                        "image_id": old_final.id,
                        "hash_id": demoted_hash_id,
                        "order": int(old_final.img_order),
                        "session_id": sess_row.id,
                        "from_state": old_from_state,
                        "to_state": "picked",
                        "session_picker_state": None,
                        "session_final_image_id": None,
                        "starred": True,
                    }
            sess_row.final_image_id = img.id

        # If the transition is *away from* 'final' on the row that owned
        # final, clear the session's final pointer.
        if (
            target != "final"
            and sess_row is not None
            and sess_row.final_image_id == img.id
        ):
            sess_row.final_image_id = None

        img.pick_state = target
        img.pick_state_updated_at = datetime.now(timezone.utc)
        # starred mirrors picked/final; everything else clears it.
        img.starred = 1 if target in ("picked", "final") else 0

        if sess_row is not None:
            await _recompute_session_state(session, sess_row)
            sess_row.updated_at = datetime.now(timezone.utc)

        # Flush so the response reads the freshly-written timestamp.
        await session.flush()

        new_pick_state = img.pick_state
        new_updated_at = _aware_utc(img.pick_state_updated_at) or datetime.now(
            timezone.utc
        )
        new_starred = bool(img.starred)
        sess_id = sess_row.id if sess_row else None
        sess_picker_state = sess_row.picker_state if sess_row else None
        sess_final_image_id = sess_row.final_image_id if sess_row else None

    # Broadcast after commit so consumers don't see uncommitted state.
    # Demotion fires first so subscribers always see the prior final
    # transition before the new final claim.
    if demotion_broadcast is not None:
        asyncio.create_task(_broadcast_pick_state(**demotion_broadcast))
    asyncio.create_task(
        _broadcast_pick_state(
            user_id=user_id,
            image_id=img.id,
            hash_id=hash_id,
            order=order,
            session_id=sess_id,
            from_state=from_state,
            to_state=new_pick_state,
            session_picker_state=sess_picker_state,
            session_final_image_id=sess_final_image_id,
            starred=new_starred,
        )
    )

    return JudgmentResponse(
        image_id=img.id,
        hash_id=hash_id,
        order=order,
        pick_state=new_pick_state,
        pick_state_updated_at=new_updated_at,
        starred=new_starred,
        session_id=sess_id,
        session_picker_state=sess_picker_state,
        session_final_image_id=sess_final_image_id,
        previous_final_image_id=previous_final_image_id,
    )


# ---------------------------------------------------------------------------
# /api/jobs/<hash>/images/<order>/{pick,discard,final,defer,unjudge}
# ---------------------------------------------------------------------------


@images_router.post(
    "/{hash_id}/images/{order}/pick", response_model=JudgmentResponse
)
async def pick_image(
    hash_id: str, order: int, user: CurrentUser
) -> JudgmentResponse:
    return await _set_pick_state(
        hash_id=hash_id, order=order, user_id=user.id, target="picked"
    )


@images_router.post(
    "/{hash_id}/images/{order}/discard", response_model=JudgmentResponse
)
async def discard_image(
    hash_id: str, order: int, user: CurrentUser
) -> JudgmentResponse:
    return await _set_pick_state(
        hash_id=hash_id, order=order, user_id=user.id, target="discarded"
    )


@images_router.post(
    "/{hash_id}/images/{order}/final", response_model=JudgmentResponse
)
async def final_image(
    hash_id: str, order: int, user: CurrentUser
) -> JudgmentResponse:
    return await _set_pick_state(
        hash_id=hash_id, order=order, user_id=user.id, target="final"
    )


@images_router.post(
    "/{hash_id}/images/{order}/defer", response_model=JudgmentResponse
)
async def defer_image(
    hash_id: str, order: int, user: CurrentUser
) -> JudgmentResponse:
    return await _set_pick_state(
        hash_id=hash_id, order=order, user_id=user.id, target="deferred"
    )


@images_router.post(
    "/{hash_id}/images/{order}/unjudge", response_model=JudgmentResponse
)
async def unjudge_image(
    hash_id: str, order: int, user: CurrentUser
) -> JudgmentResponse:
    return await _set_pick_state(
        hash_id=hash_id, order=order, user_id=user.id, target="unjudged"
    )


# ---------------------------------------------------------------------------
# Session-level operations
# ---------------------------------------------------------------------------


async def _load_owned_session(
    session, *, session_id: str, user_id: str
) -> SessionRow:
    row = (
        await session.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise api_error(
            404, "NOT_FOUND", "Session not found.", field="session_id"
        )
    return row


@sessions_router.post(
    "/{session_id}/finalize", response_model=FinalizeResponse
)
async def finalize_session(
    session_id: str, user: CurrentUser
) -> FinalizeResponse:
    async with get_session() as session:
        row = await _load_owned_session(
            session, session_id=session_id, user_id=user.id
        )

        # Idempotent: re-finalizing an already finalized session returns
        # 200 with the same payload, no SSE.
        if row.picker_state == "finalized":
            return FinalizeResponse(
                session_id=row.id,
                picker_state="finalized",
                finalized_at=_aware_utc(row.finalized_at),
                final_image_id=row.final_image_id,
            )

        # Preconditions: no SUCCEEDED image left unjudged or deferred,
        # and ``final_image_id`` must be set.
        rows = (
            await session.execute(
                select(Image.pick_state)
                .join(Job, Image.job_id == Job.id)
                .join(SessionJob, SessionJob.job_id == Job.id)
                .where(
                    SessionJob.session_id == row.id,
                    Job.status == SUCCEEDED,
                )
            )
        ).all()
        unjudged = sum(1 for (s,) in rows if s == "unjudged")
        deferred = sum(1 for (s,) in rows if s == "deferred")
        if unjudged or deferred or not row.final_image_id:
            raise api_error(
                409,
                "INVALID_PICKER_STATE",
                "Cannot finalize: unfinished judgments remain.",
                field="picker_state",
                extra={
                    "unjudged": unjudged,
                    "deferred": deferred,
                    "final_image_id": row.final_image_id,
                },
            )

        now = datetime.now(timezone.utc)
        row.picker_state = "finalized"
        row.finalized_at = now
        row.updated_at = now

    asyncio.create_task(
        _broadcast_session_finalized(
            user_id=user.id,
            session_id=session_id,
            picker_state="finalized",
            final_image_id=row.final_image_id,
        )
    )

    return FinalizeResponse(
        session_id=row.id,
        picker_state="finalized",
        finalized_at=_aware_utc(row.finalized_at),
        final_image_id=row.final_image_id,
    )


@sessions_router.post(
    "/{session_id}/unfinalize", response_model=FinalizeResponse
)
async def unfinalize_session(
    session_id: str, user: CurrentUser
) -> FinalizeResponse:
    async with get_session() as session:
        row = await _load_owned_session(
            session, session_id=session_id, user_id=user.id
        )
        # Idempotent: not-finalized → still not-finalized (200).
        if row.picker_state != "finalized":
            return FinalizeResponse(
                session_id=row.id,
                picker_state=row.picker_state,
                finalized_at=_aware_utc(row.finalized_at),
                final_image_id=row.final_image_id,
            )

        # Re-derive the natural picker_state from current image counts.
        # The 'final' image keeps its state.
        row.picker_state = "judging"
        row.finalized_at = None
        await _recompute_session_state(session, row)
        row.updated_at = datetime.now(timezone.utc)

    asyncio.create_task(
        _broadcast_session_finalized(
            user_id=user.id,
            session_id=session_id,
            picker_state=row.picker_state,
            final_image_id=row.final_image_id,
        )
    )

    return FinalizeResponse(
        session_id=row.id,
        picker_state=row.picker_state,
        finalized_at=_aware_utc(row.finalized_at),
        final_image_id=row.final_image_id,
    )


@sessions_router.patch(
    "/{session_id}/cursor", response_model=CursorPatchResponse
)
async def patch_cursor(
    session_id: str,
    body: CursorPatchRequest,
    user: CurrentUser,
) -> CursorPatchResponse:
    """Persist the user's cursor position for cross-device recovery.

    Deliberately does NOT bump ``sessions.updated_at`` — the user is just
    looking at images, not editing. SSE not broadcast either: cursor
    is intentionally per-device, not synced.
    """
    async with get_session() as session:
        row = await _load_owned_session(
            session, session_id=session_id, user_id=user.id
        )
        row.cursor_image_id = body.cursor_image_id

    return CursorPatchResponse(
        session_id=session_id, cursor_image_id=body.cursor_image_id
    )


@sessions_router.get(
    "/{session_id}/picker", response_model=PickerSessionResponse
)
async def get_session_picker(
    session_id: str, user: CurrentUser
) -> PickerSessionResponse:
    """Aggregate read for one session's picker page (PRD §9.2).

    Returns the session metadata, every job bound to the session, and
    every image attached to those jobs — plus the picker-specific
    fields (pick_state, seed, job_idx) baked in. One round trip.
    """
    async with get_session() as session:
        row = await _load_owned_session(
            session, session_id=session_id, user_id=user.id
        )

        jobs = (
            await session.execute(
                select(Job)
                .join(SessionJob, SessionJob.job_id == Job.id)
                .where(
                    SessionJob.session_id == row.id,
                    Job.user_id == user.id,
                    Job.status.in_(VISIBLE_STATUSES),
                )
                .order_by(Job.created_at, Job.hash_id)
            )
        ).scalars().all()

        # Build job_idx by created_at order so the rail can show "#3"
        # consistently across reloads.
        job_index: dict[str, int] = {j.id: i for i, j in enumerate(jobs)}

        images_rows = []
        if jobs:
            job_ids = [j.id for j in jobs]
            images_rows = (
                await session.execute(
                    select(Image)
                    .where(Image.job_id.in_(job_ids))
                    .order_by(Image.job_id, Image.img_order)
                )
            ).scalars().all()

        # Sort images globally by (job.created_at, img_order) for the
        # picker traversal order. Build a pair list before sorting.
        job_created_by_id: dict[str, datetime] = {
            j.id: _aware_utc(j.created_at) or datetime.now(timezone.utc)
            for j in jobs
        }
        sorted_images = sorted(
            images_rows,
            key=lambda im: (
                job_created_by_id.get(im.job_id, datetime.now(timezone.utc)),
                im.img_order,
            ),
        )

        hash_id_by_job: dict[str, str] = {j.id: j.hash_id for j in jobs}

        params_by_job: dict[str, dict[str, Any]] = {
            j.id: _safe_load_json(j.params_json) for j in jobs
        }

    job_views: list[PickerJobView] = []
    for j in jobs:
        params = params_by_job.get(j.id, {})
        prompt = str(params.get("prompt") or "")
        job_views.append(
            PickerJobView(
                hash_id=j.hash_id,
                status=j.status,
                model=j.model,
                model_display_name=_display_name_for(j.model),
                prompt=prompt,
                created_at=_aware_utc(j.created_at) or datetime.now(timezone.utc),
                updated_at=_aware_utc(j.updated_at) or datetime.now(timezone.utc),
                started_at=_aware_utc(j.started_at),
                finished_at=_aware_utc(j.finished_at),
            )
        )

    image_views: list[PickerImageView] = []
    for im in sorted_images:
        hash_id = hash_id_by_job.get(im.job_id, "")
        params = params_by_job.get(im.job_id, {})
        seed_value = params.get("seed")
        # Seed is sometimes int or str depending on adapter; cast.
        seed_str = str(seed_value) if seed_value is not None else None
        image_views.append(
            PickerImageView(
                image_id=im.id,
                hash_id=hash_id,
                order=int(im.img_order),
                thumb_url=f"/api/jobs/{hash_id}/images/{im.img_order}/thumb",
                download_url=f"/api/jobs/{hash_id}/images/{im.img_order}/original",
                width=int(im.width),
                height=int(im.height),
                pick_state=im.pick_state or "unjudged",
                pick_state_updated_at=_aware_utc(im.pick_state_updated_at),
                starred=bool(im.starred),
                seed=seed_str,
                job_idx=job_index.get(im.job_id, 0),
            )
        )

    sess_view = PickerSessionView(
        id=row.id,
        name=row.name,
        picker_state=row.picker_state,
        final_image_id=row.final_image_id,
        cursor_image_id=row.cursor_image_id,
        finalized_at=_aware_utc(row.finalized_at),
        created_at=_aware_utc(row.created_at) or datetime.now(timezone.utc),
        updated_at=_aware_utc(row.updated_at) or datetime.now(timezone.utc),
    )

    return PickerSessionResponse(
        session=sess_view, jobs=job_views, images=image_views
    )


# ---------------------------------------------------------------------------
# /api/picker/overview
# ---------------------------------------------------------------------------


@overview_router.get("/overview", response_model=PickerOverviewResponse)
async def get_picker_overview(user: CurrentUser) -> PickerOverviewResponse:
    """Deck-overview aggregate (PRD §9.3).

    One query for sessions, one for image-per-pickstate counts grouped
    by session, one for in-flight job counts, and one for the deck
    title preference. Total: four queries — bounded request budget per
    PRD §7.1.
    """
    deck_title = "Untitled deck"
    # Optional: check user_preferences for picker.deck_title; the field
    # doesn't exist on the prefs row as a column so we look it up via
    # ``user_preferences`` if a JSON blob has one. Today the table has
    # discrete columns only, so we just use the default.

    async with get_session() as session:
        sess_rows = (
            await session.execute(
                select(SessionRow)
                .where(SessionRow.user_id == user.id)
                .order_by(SessionRow.updated_at.desc())
            )
        ).scalars().all()

        if not sess_rows:
            return PickerOverviewResponse(
                deck_title=deck_title,
                last_edited=None,
                totals=PickerOverviewTotals(),
                summary=PickerOverviewSummary(),
                sessions=[],
            )

        session_ids = [s.id for s in sess_rows]

        # Image stats per session × pick_state.
        stats_rows = (
            await session.execute(
                select(
                    SessionJob.session_id,
                    Image.pick_state,
                    Job.status,
                )
                .join(Job, SessionJob.job_id == Job.id)
                .join(Image, Image.job_id == Job.id)
                .where(
                    SessionJob.session_id.in_(session_ids),
                    Job.status == SUCCEEDED,
                )
            )
        ).all()

        # In-flight stats per session × job.status.
        inflight_rows = (
            await session.execute(
                select(SessionJob.session_id, Job.status)
                .join(Job, SessionJob.job_id == Job.id)
                .where(
                    SessionJob.session_id.in_(session_ids),
                    Job.status.in_(("QUEUED", "RUNNING", "FAILED")),
                )
            )
        ).all()

        # Total image count per session (SUCCEEDED only — what the user
        # judges).
        # Last prompt per session: pick the prompt of the most recent
        # job. We fetch that separately via a dict.
        prompt_rows = (
            await session.execute(
                select(SessionJob.session_id, Job.params_json, Job.created_at)
                .join(Job, SessionJob.job_id == Job.id)
                .where(SessionJob.session_id.in_(session_ids))
                .order_by(SessionJob.session_id, Job.created_at.desc())
            )
        ).all()

        # Final image hash_id+order, so we can build a thumb_url.
        finals_by_session: dict[str, tuple[str, int] | None] = {
            s.id: None for s in sess_rows
        }
        finals_to_resolve = [s.final_image_id for s in sess_rows if s.final_image_id]
        if finals_to_resolve:
            finals_rows = (
                await session.execute(
                    select(Image.id, Job.hash_id, Image.img_order)
                    .join(Job, Image.job_id == Job.id)
                    .where(
                        Image.id.in_(finals_to_resolve),
                        Job.user_id == user.id,
                    )
                )
            ).all()
            id_to_url: dict[str, tuple[str, int]] = {
                img_id: (hash_id, int(order))
                for img_id, hash_id, order in finals_rows
            }
            for s in sess_rows:
                if s.final_image_id and s.final_image_id in id_to_url:
                    finals_by_session[s.id] = id_to_url[s.final_image_id]

    # Build per-session aggregates.
    stats_by_session: dict[str, PickerSessionStats] = {
        s.id: PickerSessionStats() for s in sess_rows
    }
    image_count_by_session: dict[str, int] = {s.id: 0 for s in sess_rows}
    for sid, pick_state, _status in stats_rows:
        if sid not in stats_by_session:
            continue
        stats = stats_by_session[sid]
        image_count_by_session[sid] += 1
        if pick_state == "final":
            stats.final += 1
        elif pick_state == "picked":
            stats.picked += 1
        elif pick_state == "discarded":
            stats.discarded += 1
        elif pick_state == "deferred":
            stats.deferred += 1
        else:
            stats.unjudged += 1

    inflight_by_session: dict[str, PickerInFlight] = {
        s.id: PickerInFlight() for s in sess_rows
    }
    for sid, status in inflight_rows:
        if sid not in inflight_by_session:
            continue
        inflight = inflight_by_session[sid]
        if status == "QUEUED":
            inflight.queued += 1
        elif status == "RUNNING":
            inflight.running += 1
        elif status == "FAILED":
            inflight.failed += 1

    last_prompt_by_session: dict[str, str | None] = {s.id: None for s in sess_rows}
    seen_session_for_prompt: set[str] = set()
    for sid, params_json, _created_at in prompt_rows:
        if sid in seen_session_for_prompt:
            continue
        seen_session_for_prompt.add(sid)
        params = _safe_load_json(params_json)
        last_prompt_by_session[sid] = str(params.get("prompt") or "") or None

    # Build response sessions list, in the user's "newest-first" order.
    out_sessions: list[PickerOverviewSession] = []
    summary = PickerOverviewSummary()
    totals = PickerOverviewTotals()
    last_edited: datetime | None = None
    for s in sess_rows:
        stats = stats_by_session[s.id]
        inflight = inflight_by_session[s.id]
        judged = stats.final + stats.picked + stats.discarded
        image_count = image_count_by_session[s.id]
        totals.images += image_count
        totals.judged += judged
        totals.inflight += inflight.queued + inflight.running + inflight.failed
        if s.picker_state == "finalized":
            summary.finalized += 1
        elif s.picker_state == "judging":
            # Compute ready_to_finalize at the boundary so the UI doesn't
            # need to redo the logic. A session is "ready" when no image
            # is still unjudged or deferred and we have a final pointer.
            if (
                stats.unjudged == 0
                and stats.deferred == 0
                and s.final_image_id is not None
                and image_count > 0
            ):
                summary.ready_to_finalize += 1
            else:
                summary.judging += 1
        else:
            summary.not_started += 1

        thumb_url = None
        final_pair = finals_by_session.get(s.id)
        if final_pair is not None:
            hash_id, order = final_pair
            thumb_url = f"/api/jobs/{hash_id}/images/{order}/thumb"

        sess_updated = _aware_utc(s.updated_at) or datetime.now(timezone.utc)
        if last_edited is None or sess_updated > last_edited:
            last_edited = sess_updated

        out_sessions.append(
            PickerOverviewSession(
                id=s.id,
                name=s.name,
                picker_state=s.picker_state,
                image_count=image_count,
                judged_count=judged,
                stats=stats,
                in_flight=inflight,
                final_image_id=s.final_image_id,
                final_thumb_url=thumb_url,
                last_prompt=last_prompt_by_session.get(s.id),
                updated_at=sess_updated,
            )
        )

    return PickerOverviewResponse(
        deck_title=deck_title,
        last_edited=last_edited,
        totals=totals,
        summary=summary,
        sessions=out_sessions,
    )


# ---------------------------------------------------------------------------
# /api/jobs/vary  — re-run a job with the same prompt/params but a new seed
# ---------------------------------------------------------------------------


@vary_router.post("/vary")
async def vary_seed(body: VarySeedRequest, user: CurrentUser) -> dict:
    """Spawn a new job that copies the source job's prompt/model/params
    but uses a fresh seed (PRD §6.8 method B).

    Source image -> source job. We copy ``params_json`` minus ``seed``,
    set the new seed (provided or random), reuse model + tier, and
    enqueue a fresh job bound to the same session as the source.

    References (uploads) on the source job are **not** copied — they
    were files that the executor already consumed; copying them would
    require touching the disk layout. v1 keeps it simple: vary-seed
    works for prompt-only generations (no reference images). The UI
    surfaces the button only on jobs without references.
    """
    import secrets

    from app.db.jobs_repository import get_jobs_repository, serialise_params
    from app.domain.job_queue import QueuedJob, get_job_queue
    from app.domain.job_lifecycle import QUEUED

    async with get_session() as session:
        # Find the source image, then its job.
        img = (
            await session.execute(
                select(Image).where(Image.id == body.source_image_id)
            )
        ).scalar_one_or_none()
        if img is None:
            raise api_error(
                404, "NOT_FOUND", "Source image not found.",
                field="source_image_id",
            )
        source_job = (
            await session.execute(
                select(Job).where(
                    Job.id == img.job_id, Job.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if source_job is None:
            raise api_error(
                404, "NOT_FOUND", "Source job not found.",
                field="source_image_id",
            )

        params = _safe_load_json(source_job.params_json)
        # Replace seed with the requested or a random one. Adapters
        # accept seed either as int or as a stringified value; we keep
        # the existing type if specified.
        if body.seed is not None:
            params["seed"] = body.seed
        else:
            params["seed"] = secrets.randbelow(2**31 - 1)
        # n=1 — vary-seed produces a single image; the user can chain
        # multiple variations if they want more.
        params["n"] = 1

        repo = get_jobs_repository()
        flags_json = source_job.flags_json or "{}"
        created = await repo.insert_queued(
            user_id=user.id,
            tier_at_submit=user.tier,
            model=source_job.model,
            params_json=serialise_params(params),
            flags_json=flags_json,
            client_request_id=None,
            set_id=None,
            session_id=source_job.session_id,
            session=session,
        )
        if source_job.session_id is not None:
            session.add(
                SessionJob(
                    session_id=source_job.session_id, job_id=created.job_id
                )
            )

    # Push to in-memory queue so scheduler picks it up.
    queue = get_job_queue()
    queued_job = QueuedJob(
        hash_id=created.hash_id,
        job_id=created.job_id,
        user_id=user.id,
        tier=user.tier,
        model=source_job.model,
        queued_at=_aware_utc(created.created_at) or datetime.now(timezone.utc),
        seq_no=created.seq_no,
        set_id=None,
        flags={},
    )
    await queue.enqueue(queued_job)

    # Best-effort SSE so the picker page sees the new job land.
    payload = {
        "hash_id": created.hash_id,
        "seq_no": created.seq_no,
        "model": source_job.model,
        "status": QUEUED,
        "session_id": source_job.session_id,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    asyncio.create_task(
        get_sse_hub().broadcast_to_user(user.id, "task_created", payload)
    )

    return {
        "hash_id": created.hash_id,
        "seq_no": created.seq_no,
        "session_id": source_job.session_id,
        "status": QUEUED,
    }
