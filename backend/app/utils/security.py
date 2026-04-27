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


def issue_access_token(
    user_id: str,
    username: str,
    role: str,
    *,
    expires_in_days: int | None = None,
) -> str:
    """Sign a fresh JWT for ``user_id``.

    Payload shape (design doc §2.1):

        {"sub": user_id, "u": username, "r": role, "iat": ..., "exp": ...}

    No tier / quota / display_name — those are read from DB on demand.
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
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


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
