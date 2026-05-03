"""Active auth sessions list / revoke endpoints under ``/api/me/sessions``.

The naming is deliberate even though the project also has an
``app.api.sessions`` module — that one owns archive *generation*
sessions (``sessions`` table). This module owns *auth* sessions
(``auth_sessions`` table); the URL prefix ``/api/me/sessions`` keeps
them syntactically distinct.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Response
from sqlalchemy import select, update

from app.db.engine import get_session
from app.db.models import AuthSession
from app.deps import CurrentContext
from app.schemas.me import (
    RevokeOthersResponse,
    SessionEntry,
    SessionsResponse,
)
from app.utils.audit import write_audit
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.me.sessions")

router = APIRouter(prefix="/api/me/sessions", tags=["me"])


@router.get("", response_model=SessionsResponse)
async def list_sessions(ctx: CurrentContext) -> SessionsResponse:
    user = ctx.user
    async with get_session() as session:
        rows = (
            await session.execute(
                select(AuthSession)
                .where(AuthSession.user_id == user.id)
                .where(AuthSession.revoked_at.is_(None))
                .order_by(AuthSession.last_active_at.desc())
            )
        ).scalars().all()

    current_id: str | None = None
    out: list[SessionEntry] = []
    for r in rows:
        is_current = bool(ctx.jti and r.jti == ctx.jti)
        if is_current:
            current_id = r.id
        out.append(
            SessionEntry(
                id=r.id,
                user_agent=r.user_agent,
                ip_hint=r.ip,
                created_at=r.created_at,
                last_active_at=r.last_active_at,
                is_current=is_current,
            )
        )
    return SessionsResponse(current_session_id=current_id, sessions=out)


@router.delete("/{session_id}", status_code=204)
async def revoke_session(session_id: str, ctx: CurrentContext) -> Response:
    user = ctx.user
    async with get_session() as session:
        row = (
            await session.execute(
                select(AuthSession)
                .where(AuthSession.id == session_id)
                .where(AuthSession.user_id == user.id)
            )
        ).scalar_one_or_none()
        if row is None:
            # Don't distinguish "not yours" from "doesn't exist".
            raise api_error(404, "NOT_FOUND", "Session not found.")
        if ctx.jti is not None and row.jti == ctx.jti:
            raise api_error(
                400,
                "CANNOT_REVOKE_CURRENT",
                "Cannot revoke the current session. Use Sign out instead.",
            )
        if row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            await write_audit(
                session,
                actor_user_id=user.id,
                action="me.session.revoke",
                target_kind="auth_session",
                target_id=row.id,
                payload={"ua": (row.user_agent or "")[:120]},
            )
    return Response(status_code=204)


@router.post("/revoke-others", response_model=RevokeOthersResponse)
async def revoke_others(ctx: CurrentContext) -> RevokeOthersResponse:
    user = ctx.user
    async with get_session() as session:
        q = (
            update(AuthSession)
            .where(AuthSession.user_id == user.id)
            .where(AuthSession.revoked_at.is_(None))
        )
        if ctx.jti is not None:
            q = q.where(AuthSession.jti != ctx.jti)
        q = q.values(revoked_at=datetime.now(timezone.utc))
        result = await session.execute(q)
        revoked = int(result.rowcount or 0)

        if revoked:
            await write_audit(
                session,
                actor_user_id=user.id,
                action="me.session.revoke_others",
                target_kind="user",
                target_id=user.id,
                payload={"revoked_count": revoked},
            )

    return RevokeOthersResponse(revoked_count=revoked)
