"""Password hashing + JWT signing primitives.

This module is the single home for cryptographic primitives the auth layer
relies on. Two responsibilities, kept tightly scoped so the code is easy
to audit:

1. **Password hashing** — argon2id via ``argon2-cffi``. Recommended params
   from the design doc §2.4 (t=2, m=64MB, p=1).
2. **JWT** — HS256 signed with ``Settings.JWT_SECRET``. Payload shape per
   design doc §2.1 (``sub`` / ``u`` / ``r`` / ``iat`` / ``exp``). We
   deliberately keep tier and quota out of the token; the deps re-read
   them from the DB on every request so a stale token can't escalate.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from jose import JWTError, jwt

from app.config import get_settings

# Per design doc §2.4. Argon2id is the default; t=2 / m=64MB / p=1 hits
# the OWASP-recommended balance for interactive logins on commodity hardware.
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=64 * 1024,  # KiB
    parallelism=1,
)

JWT_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(plaintext: str) -> str:
    """Return a fresh argon2id hash for ``plaintext``."""
    return _PASSWORD_HASHER.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    """Constant-time check of ``plaintext`` against an argon2id hash.

    Returns ``False`` for any verification failure (mismatch, malformed
    hash, garbage input). We never let the underlying exception escape
    because callers should not branch on *why* a password is wrong — that
    would be an oracle.
    """
    try:
        _PASSWORD_HASHER.verify(hashed, plaintext)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:
        # Defensive: any unexpected error from the hasher counts as failure.
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def new_jti() -> str:
    """Return a fresh, URL-safe random ``jti`` claim value.

    Tokens use the ``jti`` claim to bind to one row in ``auth_sessions``
    so admin-side / self-service revocation has somewhere to write.
    """
    return secrets.token_urlsafe(16)


def issue_access_token(
    user_id: str,
    username: str,
    role: str,
    *,
    expires_in_days: int | None = None,
    jti: str | None = None,
) -> str:
    """Sign a fresh JWT for ``user_id``.

    Payload shape (design doc §2.1):

        {"sub": user_id, "u": username, "r": role, "iat": ..., "exp": ...,
         "jti": ...}

    No tier / quota / display_name — those are read from DB on demand.
    The ``jti`` claim is included so the auth dependency can look up
    the session row in ``auth_sessions`` and reject revoked tokens.
    Callers either pass an explicit jti (when they're also creating the
    matching ``auth_sessions`` row) or accept the default fresh value.
    """
    settings = get_settings()
    days = expires_in_days if expires_in_days is not None else settings.JWT_EXPIRES_DAYS
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "u": username,
        "r": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=days)).timestamp()),
        "jti": jti if jti is not None else new_jti(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


# Default impersonation lifetime per design doc §13.2: 30 minutes.
IMPERSONATE_TTL_SECONDS = 30 * 60


def issue_impersonate_token(
    *,
    target_user_id: str,
    target_username: str,
    target_role: str,
    impersonator_user_id: str,
    expires_in_seconds: int = IMPERSONATE_TTL_SECONDS,
) -> tuple[str, int]:
    """Sign a short-lived JWT that lets an admin act *as* another user.

    The token's ``sub`` claim is the **target** user's id so all
    downstream auth dependencies see the request as that user. The
    extra ``impersonator`` claim carries the original admin id so
    routes that write to the audit log can record both actors
    correctly (design doc §13.11 requirement: "all impersonate
    operations in audit_log have actor=admin").

    Returns ``(token, expires_in_seconds)``. The TTL is also encoded
    in ``exp`` but we hand it back so the impersonate response can
    surface it without re-decoding.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_in_seconds = max(60, min(expires_in_seconds, IMPERSONATE_TTL_SECONDS * 2))
    payload: dict[str, Any] = {
        "sub": target_user_id,
        "u": target_username,
        "r": target_role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
        "impersonator": impersonator_user_id,
    }
    return (
        jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM),
        expires_in_seconds,
    )


class TokenError(Exception):
    """Raised when a JWT cannot be decoded, has expired, or is malformed."""


def decode_access_token(token: str) -> dict[str, Any]:
    """Validate and decode ``token``. Raises ``TokenError`` on any failure.

    The caller is responsible for re-loading the user row from the DB and
    deciding whether ``status`` / ``role`` still permit the action — this
    function only checks the signature and expiry.
    """
    settings = get_settings()
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc
