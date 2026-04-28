"""FastAPI dependency callables.

Three helpers, used as ``Depends(...)`` in the routers:

- ``get_current_user`` — decode the bearer JWT, load the user row from
  the DB, and return it. Status checks (``disabled`` / ``deleted``) are
  enforced here so every authenticated route gets them for free.
- ``get_current_admin`` — same, but additionally requires ``role=='admin'``.
- ``require_not_blocked`` — route-independent generation pause guard.
  Job-specific quota / capacity decisions still live in
  ``app.domain.access_policy`` because they need the submitted model and
  request payload.

All three return the SQLAlchemy ``User`` ORM row. Routers should not
introspect tier / quota fields directly — those belong to the domain
layer (``app.domain.access_policy``, added in PR-09).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import User
from app.domain.runtime_configs import EmergencyConfig
from app.utils.errors import api_error
from app.utils.security import TokenError, decode_access_token


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _extract_bearer(authorization: str | None) -> str:
    """Pull the bearer token out of ``Authorization: Bearer <token>``.

    Raises 401 ``UNAUTHORIZED`` if the header is missing or malformed.
    """
    if not authorization:
        raise api_error(401, "UNAUTHORIZED", "Missing Authorization header.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise api_error(401, "UNAUTHORIZED", "Malformed Authorization header.")
    return parts[1].strip()


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Return the authenticated user or raise 401/403.

    The token's ``sub`` is the user's primary key. Tier / quota / status
    are read fresh from the DB on every call — never trust JWT claims
    beyond identity (design doc §2.1).
    """
    token = _extract_bearer(authorization)

    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        raise api_error(401, "UNAUTHORIZED", "Invalid or expired token.") from exc

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise api_error(401, "UNAUTHORIZED", "Token missing subject claim.")

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()

    if user is None:
        # Token references a user that no longer exists.
        raise api_error(401, "UNAUTHORIZED", "Account not found.")
    if user.status == "disabled":
        raise api_error(403, "ACCOUNT_DISABLED", "Your account has been disabled.")
    if user.status == "deleted":
        # Soft-deleted users are functionally gone. We deliberately surface
        # the same 401 a missing user would, so admins deleting accounts
        # don't accidentally leak the soft-delete distinction.
        raise api_error(401, "UNAUTHORIZED", "Account not found.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


async def get_current_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Variant of ``get_current_user`` that additionally requires admin role."""
    if user.role != "admin":
        raise api_error(403, "FORBIDDEN", "Admin privileges required.")
    return user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


# ---------------------------------------------------------------------------
# Not-blocked guard (placeholder until PR-09)
# ---------------------------------------------------------------------------


async def require_not_blocked(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Reject generation routes while the global pause switch is on.

    FastAPI dependencies cannot see the submitted model / multipart
    payload, so the complete PR-09 gate is exposed as
    ``AccessPolicy.evaluate(user, model=...)`` for job routes to call
    explicitly. This dependency covers the route-independent emergency
    switch for handlers that only need "generation is not paused".
    """
    if EmergencyConfig().pause_generation:
        raise api_error(
            403,
            "BLOCKED_BY_EMERGENCY",
            "Service temporarily paused by admin.",
        )
    return user
