"""``GET /api/models`` — Create-page model picker (PR-13, design doc §6.2).

Returns the list of models the authenticated user can request, the
effective capability union for each, recommended defaults, and the
user's session list (so the Create page can render the "Bind to
session" picker without a second round-trip).

The route is a thin wrapper around :mod:`app.domain.model_catalog` and
:mod:`app.api.sessions` — heavy lifting lives there.
"""

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from app.db.engine import get_session
from app.db.models import Session as SessionRow
from app.db.models import SessionJob
from app.deps import CurrentUser
from app.domain.model_catalog import list_models_for_user
from app.schemas.models import ModelSessionEntry, ModelsResponse

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models", response_model=ModelsResponse)
async def get_models(user: CurrentUser) -> ModelsResponse:
    """Return the user's available models + sessions.

    The frontend calls this on Create-page mount, again every 60s if
    the user lingers without submitting (so capability changes from
    admin edits propagate without a refresh), and on receipt of the
    SSE ``model_capabilities_changed`` event.
    """
    descriptors = await list_models_for_user(user)
    sessions = await _list_user_sessions(user.id)
    return ModelsResponse(models=descriptors, sessions=sessions)


async def _list_user_sessions(user_id: str) -> list[ModelSessionEntry]:
    """Return the user's sessions, newest first, with image counts.

    Mirrors the aggregation in :func:`app.api.sessions.list_sessions` but
    returns the lighter ``ModelSessionEntry`` shape — we don't need the
    full ``SessionResponse`` here.
    """
    async with get_session() as session:
        rows = (
            await session.execute(
                select(SessionRow)
                .where(SessionRow.user_id == user_id)
                .order_by(SessionRow.updated_at.desc())
            )
        ).scalars().all()
        if not rows:
            return []

        ids = [r.id for r in rows]
        count_rows = (
            await session.execute(
                select(SessionJob.session_id, func.count(SessionJob.job_id))
                .where(SessionJob.session_id.in_(ids))
                .group_by(SessionJob.session_id)
            )
        ).all()

    counts = {sid: int(c) for sid, c in count_rows}
    out: list[ModelSessionEntry] = []
    for r in rows:
        ts = r.updated_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(
            ModelSessionEntry(
                id=r.id,
                name=r.name,
                image_count=counts.get(r.id, 0),
                updated_at=ts.isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
        )
    return out
