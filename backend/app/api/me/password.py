"""``POST /api/me/password`` — self-service password change.

On success we revoke every other auth_session for the user (so a
stolen device session is invalidated) but keep the calling session
intact so the user doesn't get bounced to the login screen mid-edit.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Response
from sqlalchemy import select, update

from app.db.engine import get_session
from app.db.models import AuthSession, User
from app.deps import CurrentContext
from app.schemas.me import ChangePasswordRequest
from app.utils.audit import write_audit
from app.utils.errors import api_error
from app.utils.security import hash_password, verify_password

logger = logging.getLogger("txt2img.me.password")

router = APIRouter(prefix="/api/me/password", tags=["me"])


_LETTERS = re.compile(r"[A-Za-z]")
_DIGITS = re.compile(r"[0-9]")
_SYMBOLS = re.compile(r"[^A-Za-z0-9]")


def _evaluate_password(new_password: str, current_password: str) -> None:
    if len(new_password) < 10:
        raise api_error(
            422,
            "PASSWORD_POLICY",
            "New password must be at least 10 characters.",
            field="new_password",
        )
    classes = sum(
        1
        for rx in (_LETTERS, _DIGITS, _SYMBOLS)
        if rx.search(new_password) is not None
    )
    if classes < 2:
        raise api_error(
            422,
            "PASSWORD_POLICY",
            "New password must mix at least two of letters, digits, and symbols.",
            field="new_password",
        )
    if new_password == current_password:
        raise api_error(
            422,
            "PASSWORD_REUSED",
            "New password cannot be the same as the current password.",
            field="new_password",
        )


@router.post("", status_code=204)
async def change_password(body: ChangePasswordRequest, ctx: CurrentContext) -> Response:
    user = ctx.user

    async with get_session() as session:
        fresh = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()

        if not verify_password(body.current_password, fresh.password_hash):
            raise api_error(
                401,
                "WRONG_CURRENT_PASSWORD",
                "Current password is incorrect.",
                field="current_password",
            )

        _evaluate_password(body.new_password, body.current_password)

        fresh.password_hash = hash_password(body.new_password)
        fresh.password_changed_at = datetime.now(timezone.utc)

        # Revoke every other active session for this user. We deliberately
        # keep the current session alive so the user isn't bounced.
        if ctx.jti is not None:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id)
                .where(AuthSession.jti != ctx.jti)
                .where(AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(timezone.utc))
            )
        else:
            # No jti on the current token (legacy / impersonate); revoke
            # all active sessions to be safe.
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id)
                .where(AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(timezone.utc))
            )

        await write_audit(
            session,
            actor_user_id=user.id,
            action="me.password.change",
            target_kind="user",
            target_id=user.id,
        )

    return Response(status_code=204)
