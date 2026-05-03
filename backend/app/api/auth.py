"""Authentication endpoints.

Three public, unauthenticated routes plus the authenticated ``/api/me``
identity probe and a no-op ``/api/auth/logout``. Mapping to the design doc:

- ``POST /api/auth/captcha-check`` — §2.3 step 1; tells the frontend whether
  to render the Turnstile widget. Decision based on (a) failed-login count
  in the rolling 5-minute window, (b) the ``FORCE_CAPTCHA`` env flag, and
  (c) the ``emergency.force_captcha_global`` row in ``config``.
- ``POST /api/auth/login`` — §2.3 step 2; verifies password + optional
  captcha, records the attempt either way, and signs a JWT on success.
- ``POST /api/auth/logout`` — §16.1; sessionless backend so this is just
  a 200 to give the frontend a clean hook.
- ``GET /api/me`` — §16.1; returns identity only, never tier or quota.

Failure-counting: a "fresh failure window" starts after every successful
login. We count failed attempts only since the last success (within 5
minutes), so a single successful login fully resets the captcha
requirement without needing a separate cleanup job.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import AuthSession, Config, LoginAttempt, User
from app.deps import CurrentContext
from app.schemas.auth import (
    CaptchaCheckRequest,
    CaptchaCheckResponse,
    LoginRequest,
    LoginResponse,
    LoginUser,
    MeResponse,
)
from app.services import turnstile
from app.utils.errors import api_error
from app.utils.ids import new_auth_session_id
from app.utils.security import issue_access_token, new_jti, verify_password

logger = logging.getLogger("txt2img.auth")

router = APIRouter(prefix="/api", tags=["auth"])


# Configurable in spirit; pinned in code because the design doc fixes them.
FAILURE_WINDOW_SECONDS = 5 * 60
FAILURE_THRESHOLD = 3
FORCE_CAPTCHA_GLOBAL_KEY = "emergency.force_captcha_global"
BLOCK_NEW_MEMBER_LOGIN_KEY = "emergency.block_new_member_login"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _config_bool(key: str) -> bool:
    """Read one boolean emergency switch from ``config``.

    Returns False if the row is missing or unparseable — admin emergency
    switches must be deliberate. We don't cache here; the lookup is one
    primary-key hit per login attempt and keeps tests that mutate config
    directly through the DB deterministic.
    """
    async with get_session() as session:
        row = (
            await session.execute(
                select(Config).where(Config.key == key)
            )
        ).scalar_one_or_none()
    if row is None:
        return False
    try:
        return bool(json.loads(row.value_json))
    except (ValueError, TypeError):
        return False


async def _force_captcha_global() -> bool:
    """Return the global captcha emergency switch."""
    return await _config_bool(FORCE_CAPTCHA_GLOBAL_KEY)


async def _block_new_member_login() -> bool:
    """Return the login-block emergency switch."""
    return await _config_bool(BLOCK_NEW_MEMBER_LOGIN_KEY)


async def _recent_failed_attempts(username: str) -> int:
    """Count failed login attempts in the last ``FAILURE_WINDOW_SECONDS``.

    "Failed" means ``success=0``. Successful attempts in the same window
    reset the counter — we look at attempts strictly *after* the last
    success.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=FAILURE_WINDOW_SECONDS)
    async with get_session() as session:
        # Most recent successful attempt within the window — nominal case is
        # nothing, in which case all failures in the window count.
        last_success = (
            await session.execute(
                select(LoginAttempt.attempted_at)
                .where(LoginAttempt.username == username)
                .where(LoginAttempt.success == 1)
                .where(LoginAttempt.attempted_at >= cutoff)
                .order_by(LoginAttempt.attempted_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        q = (
            select(LoginAttempt.id)
            .where(LoginAttempt.username == username)
            .where(LoginAttempt.success == 0)
            .where(LoginAttempt.attempted_at >= cutoff)
        )
        if last_success is not None:
            q = q.where(LoginAttempt.attempted_at > last_success)
        rows = (await session.execute(q)).all()
    return len(rows)


async def _record_attempt(username: str, ip: str | None, success: bool) -> None:
    async with get_session() as session:
        session.add(
            LoginAttempt(
                username=username,
                ip=ip,
                success=1 if success else 0,
            )
        )


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP extraction.

    We trust ``X-Forwarded-For`` because the production deployment runs
    behind nginx (per the repo's ``docker-compose.prod.yml``); for direct
    LAN hits we fall back to the socket peer.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/auth/captcha-check", response_model=CaptchaCheckResponse)
async def captcha_check(body: CaptchaCheckRequest) -> CaptchaCheckResponse:
    """Tell the frontend whether to show Turnstile before login."""
    settings = get_settings()

    if settings.FORCE_CAPTCHA:
        return CaptchaCheckResponse(
            captcha_required=True,
            captcha_provider="turnstile",
            site_key=settings.TURNSTILE_SITE_KEY or None,
            reason="force_captcha",
        )

    if await _force_captcha_global():
        return CaptchaCheckResponse(
            captcha_required=True,
            captcha_provider="turnstile",
            site_key=settings.TURNSTILE_SITE_KEY or None,
            reason="force_captcha",
        )

    failures = await _recent_failed_attempts(body.username)
    if failures >= FAILURE_THRESHOLD:
        return CaptchaCheckResponse(
            captcha_required=True,
            captcha_provider="turnstile",
            site_key=settings.TURNSTILE_SITE_KEY or None,
            reason="failure_threshold_exceeded",
        )

    return CaptchaCheckResponse(captcha_required=False)


def _user_agent(request: Request) -> str | None:
    raw = request.headers.get("user-agent")
    if not raw:
        return None
    return raw[:512]


def _ip_hint(ip: str | None) -> str | None:
    """Coarsen an IPv4 / IPv6 address before storing it.

    The Active Sessions UI shows ``121.43.x.x`` rather than the precise
    address — enough to recognise "I logged in from home" without
    revealing precise location to anyone who reads the audit log.
    """
    if not ip:
        return None
    if ":" in ip:
        # IPv6 — keep first two hextets only.
        parts = ip.split(":")
        cleaned = [p for p in parts if p]
        if len(cleaned) >= 2:
            return f"{cleaned[0]}:{cleaned[1]}::/32"
        return ip
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    return ip


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """Verify credentials, optionally validate captcha, return a JWT.

    Order of checks is deliberate:
        1. Lookup user (timing-side-channel acceptable; same as 99% of the
           industry). Treat missing user as a bad-password failure.
        2. Verify password.
        3. If captcha was required, validate it. We *don't* short-circuit
           on captcha alone before password — if the password is wrong we
           always return the same 401 regardless of captcha state.
        4. Disabled accounts: 403 ``ACCOUNT_DISABLED`` (after credentials
           pass; we don't leak existence to anonymous callers).
    """
    settings = get_settings()
    ip = _client_ip(request)

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.username == body.username))
        ).scalar_one_or_none()

    captcha_was_required = (
        settings.FORCE_CAPTCHA
        or await _force_captcha_global()
        or await _recent_failed_attempts(body.username) >= FAILURE_THRESHOLD
    )

    # ----- credential check (always run, never short-circuit on missing user)
    valid_password = bool(
        user is not None
        and user.status != "deleted"
        and verify_password(body.password, user.password_hash)
    )

    if not valid_password:
        await _record_attempt(body.username, ip, success=False)
        raise api_error(401, "UNAUTHORIZED", "Invalid username or password.")

    # ----- emergency login block (admins bypass)
    assert user is not None  # narrow for type-checker; valid_password implies this
    if user.role != "admin" and await _block_new_member_login():
        raise api_error(
            401,
            "BLOCKED_BY_EMERGENCY",
            "Service temporarily paused by admin.",
        )

    # ----- captcha check (only after credentials pass)
    if captcha_was_required:
        if not body.captcha_token:
            await _record_attempt(body.username, ip, success=False)
            raise api_error(
                412,
                "CAPTCHA_REQUIRED",
                "Captcha verification is required for this login.",
            )
        captcha_ok = await turnstile.verify(body.captcha_token, remote_ip=ip)
        if not captcha_ok:
            await _record_attempt(body.username, ip, success=False)
            raise api_error(412, "CAPTCHA_INVALID", "Captcha verification failed.")

    # ----- account-status gate (after credentials so we don't leak existence)
    if user.status == "disabled":
        await _record_attempt(body.username, ip, success=False)
        raise api_error(403, "ACCOUNT_DISABLED", "Your account has been disabled.")

    # ----- success
    await _record_attempt(body.username, ip, success=True)
    jti = new_jti()
    ua = _user_agent(request)
    ip_hint = _ip_hint(ip)
    async with get_session() as session:
        # Refresh inside its own session so the timestamp update is committed.
        # We re-fetch by id to avoid carrying a detached instance from above.
        fresh = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()
        fresh.last_login_at = datetime.now(timezone.utc)
        # Track this session server-side so the user can see and revoke
        # it from /settings → Security. The id format (``as_…``) is
        # distinct from the archive ``sessions`` table (``sess_…``) so
        # the two tables can never be confused.
        session.add(
            AuthSession(
                id=new_auth_session_id(),
                user_id=user.id,
                jti=jti,
                user_agent=ua,
                ip=ip_hint,
            )
        )

    token = issue_access_token(user.id, user.username, user.role, jti=jti)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        user=LoginUser(
            id=user.id,
            username=user.username,
            role=user.role,
            display_name=user.display_name,
        ),
    )


@router.post("/auth/logout")
async def logout(ctx: CurrentContext) -> dict[str, bool]:
    """Revoke the current session's auth_sessions row.

    The frontend still discards the token client-side; this server-side
    flip is what makes "I want to actually log this device out" stick
    even when the JWT is still cryptographically valid for days.
    """
    if ctx.jti is not None and ctx.impersonator_id is None:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(AuthSession).where(AuthSession.jti == ctx.jti)
                )
            ).scalar_one_or_none()
            if row is not None and row.revoked_at is None:
                row.revoked_at = datetime.now(timezone.utc)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(ctx: CurrentContext) -> MeResponse:
    """Return identity-only profile.

    Tier, today_count, soft_quota, hard_quota are deliberately absent —
    the frontend never gets to read them (design doc §3.3). ``email``,
    ``created_at``, ``last_login_at``, ``password_changed_at`` were
    added with the /settings page and are safe profile metadata.
    """
    user = ctx.user
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        display_name=user.display_name,
        email=user.email,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        password_changed_at=user.password_changed_at,
    )
