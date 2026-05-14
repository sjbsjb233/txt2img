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

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import AuthSession, User
from app.domain.runtime_configs import EmergencyConfig
from app.utils import log_context
from app.utils.errors import api_error
from app.utils.security import TokenError, decode_access_token


logger = logging.getLogger("txt2img.deps.auth")


# ---------------------------------------------------------------------------
# Auth context — caller-visible bundle of "who is acting & on whose behalf"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthContext:
    """The fully-resolved auth state for one authenticated request.

    Most route handlers don't need this — they just take ``CurrentUser``
    and behave as that user. Admin handlers that emit audit log rows
    use ``CurrentAdminContext`` instead so they can record the original
    admin id even when the request was signed by an impersonate token.
    """

    user: User
    """The user the request is acting *as*. ``token.sub``-based."""

    impersonator_id: str | None
    """Admin id from the JWT ``impersonator`` claim, or ``None`` for a
    plain login token. When set, ``user`` is the *target* of the
    impersonation, not the original actor.
    """

    jti: str | None
    """``jti`` claim from the JWT, when present. Used by the /api/me
    routes to know which auth_sessions row represents *this* request
    so they can label it as the current device.
    """

    @property
    def is_impersonating(self) -> bool:
        return self.impersonator_id is not None


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _extract_bearer(authorization: str | None) -> str:
    """Pull the bearer token out of ``Authorization: Bearer <token>``.

    Raises 401 ``UNAUTHORIZED`` if the header is missing or malformed.
    """
    if not authorization:
        logger.warning("auth: missing Authorization header")
        raise api_error(401, "UNAUTHORIZED", "Missing Authorization header.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        logger.warning("auth: malformed Authorization header")
        raise api_error(401, "UNAUTHORIZED", "Malformed Authorization header.")
    return parts[1].strip()


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


async def get_auth_context(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """Decode the bearer token and resolve the request's auth context.

    Returns an :class:`AuthContext` carrying both the acting user (the
    ``sub`` claim's user row) and, when present, the admin id from the
    ``impersonator`` claim. Status checks live here so every
    authenticated path inherits them.

    Raises 401/403 with the §17 error codes:
    - missing / malformed / invalid / expired bearer → 401 UNAUTHORIZED
    - account disabled → 403 ACCOUNT_DISABLED
    - account soft-deleted → 401 UNAUTHORIZED (we deliberately don't
      leak the soft-delete distinction to an anonymous-ish caller)
    """
    token = _extract_bearer(authorization)

    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        logger.warning("auth: token decode failed reason=%s", exc)
        raise api_error(401, "UNAUTHORIZED", "Invalid or expired token.") from exc

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        logger.warning("auth: token missing subject claim")
        raise api_error(401, "UNAUTHORIZED", "Token missing subject claim.")

    impersonator_raw = payload.get("impersonator")
    impersonator_id: str | None
    if isinstance(impersonator_raw, str) and impersonator_raw:
        impersonator_id = impersonator_raw
    else:
        impersonator_id = None

    jti_raw = payload.get("jti")
    jti: str | None = jti_raw if isinstance(jti_raw, str) and jti_raw else None

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        # Plain login tokens carry a jti tied to a row in auth_sessions.
        # If that row has been revoked, the token is no longer valid even
        # though the cryptographic signature still verifies. Impersonate
        # tokens skip the lookup — they're short-lived, single-use admin
        # tools and don't need device tracking.
        if jti is not None and impersonator_id is None:
            sess_row = (
                await session.execute(
                    select(AuthSession).where(AuthSession.jti == jti)
                )
            ).scalar_one_or_none()
            if sess_row is not None and sess_row.revoked_at is not None:
                logger.warning(
                    "auth: session revoked jti=%s user_id=%s", jti, user_id
                )
                raise api_error(401, "UNAUTHORIZED", "Session has been revoked.")

    if user is None:
        # Token references a user that no longer exists.
        logger.warning("auth: token user not found user_id=%s", user_id)
        raise api_error(401, "UNAUTHORIZED", "Account not found.")
    if user.status == "disabled":
        logger.warning("auth: account_disabled user_id=%s", user.id)
        raise api_error(403, "ACCOUNT_DISABLED", "Your account has been disabled.")
    if user.status == "deleted":
        # Soft-deleted users are functionally gone. We deliberately surface
        # the same 401 a missing user would, so admins deleting accounts
        # don't accidentally leak the soft-delete distinction.
        logger.warning("auth: account_deleted user_id=%s", user.id)
        raise api_error(401, "UNAUTHORIZED", "Account not found.")

    # Stamp the request-scoped log context. Downstream ``logger.*`` calls
    # in the request handler will now carry ``user_id`` automatically.
    log_context.set_user_id(user.id)
    if impersonator_id is not None:
        logger.info(
            "auth: impersonation request user_id=%s impersonator_id=%s",
            user.id,
            impersonator_id,
        )

    return AuthContext(user=user, impersonator_id=impersonator_id, jti=jti)


async def get_current_user(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> User:
    """Return the authenticated user or raise 401/403.

    The token's ``sub`` is the user's primary key. Tier / quota / status
    are read fresh from the DB on every call — never trust JWT claims
    beyond identity (design doc §2.1). Routes that need to know whether
    the request was signed by an impersonate token should depend on
    :func:`get_auth_context` directly instead.
    """
    return ctx.user


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentContext = Annotated[AuthContext, Depends(get_auth_context)]


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


async def get_current_admin(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> User:
    """Variant of ``get_current_user`` that additionally requires admin role.

    Critically, we reject **impersonate tokens** here regardless of the
    target's role: an admin who impersonates another user must not also
    retain admin privileges through the impersonate token, otherwise
    ``/api/admin/*`` writes during impersonation would skirt the audit
    chain (the actor would resolve to whatever user owns the impersonate
    session, not the admin who started it).
    """
    if ctx.impersonator_id is not None:
        logger.warning(
            "auth: admin endpoint denied during impersonation user_id=%s impersonator_id=%s",
            ctx.user.id,
            ctx.impersonator_id,
        )
        raise api_error(
            403,
            "FORBIDDEN",
            "Admin endpoints are not available while impersonating a user.",
        )
    if ctx.user.role != "admin":
        logger.warning(
            "auth: admin required, denied user_id=%s role=%s",
            ctx.user.id,
            ctx.user.role,
        )
        raise api_error(403, "FORBIDDEN", "Admin privileges required.")
    return ctx.user


async def get_current_admin_context(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Like :func:`get_current_admin` but exposes the full context.

    Currently identical to ``get_current_admin`` because impersonation
    is forbidden at the admin boundary, but admin handlers that want
    to assert / log on the IP or audit chain can take this dependency
    directly without re-deriving the context.
    """
    if ctx.impersonator_id is not None:
        logger.warning(
            "auth: admin context denied during impersonation user_id=%s impersonator_id=%s",
            ctx.user.id,
            ctx.impersonator_id,
        )
        raise api_error(
            403,
            "FORBIDDEN",
            "Admin endpoints are not available while impersonating a user.",
        )
    if ctx.user.role != "admin":
        logger.warning(
            "auth: admin context required, denied user_id=%s role=%s",
            ctx.user.id,
            ctx.user.role,
        )
        raise api_error(403, "FORBIDDEN", "Admin privileges required.")
    return ctx


CurrentAdmin = Annotated[User, Depends(get_current_admin)]
CurrentAdminContext = Annotated[AuthContext, Depends(get_current_admin_context)]


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
        logger.warning(
            "auth: generation paused by emergency switch user_id=%s", user.id
        )
        raise api_error(
            403,
            "BLOCKED_BY_EMERGENCY",
            "Service temporarily paused by admin.",
        )
    return user
