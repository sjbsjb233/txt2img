"""``PATCH /api/me`` — display name + email."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import User
from app.deps import CurrentUser
from app.schemas.auth import MeResponse
from app.schemas.me import MeProfileUpdate
from app.utils.audit import write_audit
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.me.profile")

router = APIRouter(prefix="/api/me", tags=["me"])


# Email validation: deliberately conservative — we just need a sanity
# check that this looks like an address. Full RFC compliance is a
# tarpit and we don't gate anything sensitive on the email value (no
# verification flow yet).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DISPLAY_NAME_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")


@router.patch("", response_model=MeResponse)
async def patch_me(body: MeProfileUpdate, user: CurrentUser) -> MeResponse:
    fields_changed: list[str] = []

    new_display_name: str | None = None
    if body.display_name is not None:
        candidate = body.display_name.strip()
        if not candidate:
            raise api_error(
                422,
                "INVALID_DISPLAY_NAME",
                "Display name cannot be empty.",
                field="display_name",
            )
        if len(candidate) > 50:
            raise api_error(
                422,
                "INVALID_DISPLAY_NAME",
                "Display name cannot exceed 50 characters.",
                field="display_name",
            )
        if _DISPLAY_NAME_FORBIDDEN.search(candidate):
            raise api_error(
                422,
                "INVALID_DISPLAY_NAME",
                "Display name cannot contain control characters.",
                field="display_name",
            )
        new_display_name = candidate

    new_email: str | None | type(...) = ...  # ``...`` means "unchanged"
    if body.email is not None:
        # Empty string clears the address; treat as null.
        candidate_email = body.email.strip()
        if candidate_email == "":
            new_email = None
        else:
            if len(candidate_email) > 200:
                raise api_error(
                    422,
                    "INVALID_EMAIL",
                    "Email is too long.",
                    field="email",
                )
            if not _EMAIL_RE.match(candidate_email):
                raise api_error(
                    422,
                    "INVALID_EMAIL",
                    "Email format is not valid.",
                    field="email",
                )
            new_email = candidate_email

    async with get_session() as session:
        fresh = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()

        if new_display_name is not None and fresh.display_name != new_display_name:
            fresh.display_name = new_display_name
            fields_changed.append("display_name")

        if new_email is not ...:
            if new_email != fresh.email:
                if new_email is not None:
                    # Uniqueness check — case-sensitive equality is fine
                    # at the storage layer; we don't normalise email
                    # case so the user sees what they typed back.
                    clash = (
                        await session.execute(
                            select(User.id)
                            .where(User.email == new_email)
                            .where(User.id != user.id)
                        )
                    ).scalar_one_or_none()
                    if clash is not None:
                        raise api_error(
                            409,
                            "EMAIL_TAKEN",
                            "That email is already in use.",
                            field="email",
                        )
                fresh.email = new_email
                fields_changed.append("email")

        if fields_changed:
            await write_audit(
                session,
                actor_user_id=user.id,
                action="me.update",
                target_kind="user",
                target_id=user.id,
                payload={"fields_changed": fields_changed},
            )

        return MeResponse(
            id=fresh.id,
            username=fresh.username,
            role=fresh.role,
            display_name=fresh.display_name,
            email=fresh.email,
            created_at=fresh.created_at,
            last_login_at=fresh.last_login_at,
            password_changed_at=fresh.password_changed_at,
        )
