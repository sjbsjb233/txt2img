"""Request / response shapes for ``app.api.auth``.

Schema is the public contract with the frontend. Keep field names stable;
breaking them requires a coordinated frontend change.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# captcha-check
# ---------------------------------------------------------------------------


class CaptchaCheckRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)


class CaptchaCheckResponse(BaseModel):
    captcha_required: bool
    captcha_provider: str | None = None
    site_key: str | None = None
    # Reason the captcha was required, for debugging / UX hinting.
    # One of: "failure_threshold_exceeded" / "force_captcha" / null.
    reason: str | None = None


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)
    captcha_token: str | None = None


class LoginUser(BaseModel):
    """Subset of the User row we return on login.

    Per design doc §2.3 we deliberately omit tier / quota / today_count.
    """

    id: str
    username: str
    role: str
    display_name: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: LoginUser


# ---------------------------------------------------------------------------
# /api/me
# ---------------------------------------------------------------------------


class MeResponse(BaseModel):
    id: str
    username: str
    role: str
    display_name: str | None = None
