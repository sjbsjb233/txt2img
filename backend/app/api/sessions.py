"""User-owned session (image-group) endpoints — design doc §6.6 / §16.4.

Sessions are a thin tag layer on top of jobs: a user can create a
session (``POST``), bind jobs into it at job-create time (handled in
PR-13's ``POST /api/jobs``), rename it (``PATCH``), and delete it
(``DELETE``). Deleting a session never deletes the jobs inside it —
the join rows in ``session_jobs`` are dropped via ``ON DELETE CASCADE``
and the jobs themselves keep their ``session_id`` reference cleared.

Why these routes exist in PR-08 even though the rest of the job
lifecycle ships later: archive filtering by session is part of the
read API surface and the frontend's Create page renders a "Bind to
session" picker. Both depend on the list / create endpoints being
available before PR-13 wires job creation, so we land them with the
data-model PR.

Authorisation: every route gates on :data:`CurrentUser` and additionally
checks the session belongs to the caller. Cross-tenant access returns
404 (not 403) so we don't reveal whether the id exists.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import delete, func, select, update

from app.db.engine import get_session
from app.db.models import Session as SessionRow
from app.db.models import SessionJob
from app.deps import CurrentUser
from app.domain.sse_hub import get_sse_hub
from app.schemas.sessions import (
    SessionCreateRequest,
    SessionListResponse,
    SessionPatchRequest,
    SessionResponse,
)
from app.utils.errors import api_error
from app.utils.ids import new_session_id

logger = logging.getLogger("txt2img.sessions")

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_response(row: SessionRow, *, image_count: int) -> SessionResponse:
    """Project a ``Session`` ORM row into the API response shape.

    Extracted so the list / create / patch routes share a single
    construction site. ``image_count`` is computed by the caller —
    create / patch always pass 0 / latest count, list does a single
    aggregation query for all rows at once.
    """
    return SessionResponse(
        id=row.id,
        name=row.name,
        image_count=image_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=SessionListResponse)
async def list_sessions(user: CurrentUser) -> SessionListResponse:
    """Return every session that belongs to the caller, newest first.

    Sort order is ``updated_at DESC`` so the user's most recently
    touched group bubbles to the top — the same ordering the design
    doc's archive sidebar expects. ``image_count`` per session is
    aggregated in one query against ``session_jobs`` to avoid N+1.
    """
    async with get_session() as session:
        rows = (
            await session.execute(
                select(SessionRow)
                .where(SessionRow.user_id == user.id)
                .order_by(SessionRow.updated_at.desc())
            )
        ).scalars().all()

        if not rows:
            return SessionListResponse(sessions=[])

        # One pass for image counts: GROUP BY session_id over the join
        # table, restricted to sessions we actually care about.
        session_ids = [r.id for r in rows]
        count_rows = (
            await session.execute(
                select(SessionJob.session_id, func.count(SessionJob.job_id))
                .where(SessionJob.session_id.in_(session_ids))
                .group_by(SessionJob.session_id)
            )
        ).all()

    counts: dict[str, int] = {sid: int(c) for sid, c in count_rows}
    return SessionListResponse(
        sessions=[
            _row_to_response(r, image_count=counts.get(r.id, 0)) for r in rows
        ]
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreateRequest,
    user: CurrentUser,
) -> SessionResponse:
    """Create a fresh, empty session owned by the caller.

    There is no per-name uniqueness — a user is free to keep multiple
    sessions called "Drafts" if they want; the canonical id is what the
    frontend stores. Storing the create + update timestamps as the
    same value keeps "newest" sorting sensible until the first
    ``PATCH`` bumps ``updated_at``.
    """
    now = datetime.now(timezone.utc)
    row = SessionRow(
        id=new_session_id(),
        user_id=user.id,
        name=body.name,
        created_at=now,
        updated_at=now,
    )
    async with get_session() as session:
        session.add(row)

    return _row_to_response(row, image_count=0)


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@router.patch("/{session_id}", response_model=SessionResponse)
async def patch_session(
    session_id: str,
    body: SessionPatchRequest,
    user: CurrentUser,
) -> SessionResponse:
    """Partial update of one session. Today only ``name`` is mutable.

    The body is validated by pydantic; we reject empty bodies here
    because a no-op PATCH masks the intent and would otherwise still
    bump ``updated_at`` (which would silently shuffle the list order).
    """
    set_fields = body.model_fields_set
    if not set_fields:
        raise api_error(
            400, "BAD_REQUEST", "PATCH body must contain at least one field."
        )

    # ``name`` is the only mutable field today, but the schema declares
    # it as ``str | None`` so pydantic accepts ``{"name": null}`` (which
    # is technically a non-empty body — ``set_fields`` contains "name"
    # — yet nothing is actually being asked to change). Letting that
    # request through would silently bump ``updated_at`` and reorder
    # the user's session list, which is surprising. Reject it as
    # 422 INVALID_PARAMETER instead. Field omission still works (the
    # field isn't in ``set_fields`` then).
    if "name" in set_fields and body.name is None:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "'name' cannot be null.",
            field="name",
        )

    async with get_session() as session:
        row = (
            await session.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            # 404 (not 403) on cross-tenant access — don't leak existence.
            raise api_error(
                404, "NOT_FOUND", "Session not found.", field="session_id"
            )

        if "name" in set_fields and body.name is not None:
            row.name = body.name

        row.updated_at = datetime.now(timezone.utc)

        # Compute the image_count for the response. Cheap because we
        # already have the session id and the count lives in a tight
        # join table with a small per-user fanout.
        image_count = (
            (
                await session.execute(
                    select(func.count(SessionJob.job_id)).where(
                        SessionJob.session_id == session_id
                    )
                )
            ).scalar_one()
        )

    return _row_to_response(row, image_count=int(image_count or 0))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user: CurrentUser,
) -> dict[str, bool]:
    """Drop one session. Jobs are preserved.

    We delete the parent ``sessions`` row; the schema's ``ON DELETE
    CASCADE`` on ``session_jobs`` removes the join rows automatically.
    The associated ``jobs`` rows still reference the (now-orphan)
    session id via ``jobs.session_id`` — we explicitly clear that
    column for affected jobs so the archive view doesn't show a stale
    badge for a session that no longer exists.

    Idempotent in the "missing returns 404" sense: hitting DELETE on
    an id that is not yours, or doesn't exist, returns 404. Hitting
    DELETE on a real id twice returns 404 the second time.
    """
    async with get_session() as session:
        row = (
            await session.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise api_error(
                404, "NOT_FOUND", "Session not found.", field="session_id"
            )

        await session.delete(row)

        # Clear the dangling reference on jobs that pointed at this
        # session. Using a bulk UPDATE keeps the cost flat in the size
        # of the bound set — we never load Job rows into the ORM.
        from app.db.models import Job

        await session.execute(
            update(Job)
            .where(Job.session_id == session_id, Job.user_id == user.id)
            .values(session_id=None)
        )

        # Belt + suspenders: the FK declares ON DELETE CASCADE on
        # session_jobs but SQLite only enforces foreign keys when the
        # ``foreign_keys`` PRAGMA is on (it is, see ``db.engine``).
        # Issue an explicit DELETE so a future migration that flips
        # the cascade off doesn't silently leave orphan rows.
        await session.execute(
            delete(SessionJob).where(SessionJob.session_id == session_id)
        )

    # Notify any open Picker tabs so they can show the
    # "session was deleted" overlay (PRD §12.3 B14).
    asyncio.create_task(_broadcast_session_deleted(user.id, session_id))

    return {"ok": True}


async def _broadcast_session_deleted(user_id: str, session_id: str) -> None:
    payload = {
        "session_id": session_id,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        await get_sse_hub().broadcast_to_user(user_id, "session_deleted", payload)
    except Exception:  # pragma: no cover — best effort
        logger.exception("session_deleted broadcast failed for %s", session_id)
