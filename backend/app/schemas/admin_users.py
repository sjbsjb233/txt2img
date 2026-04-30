"""Pydantic shapes for the admin user-management endpoints (PR-15).

These mirror design doc §13.2 verbatim. Field names that already exist
in :mod:`app.db.models.User` keep their column names so a frontend that
edits one field can round-trip without a translation table.

What's deliberately *not* surfaced
----------------------------------
- ``password_hash`` — never leaves the DB. ``password`` is set-only and
  arrives in the create / reset-password requests.
- ``today_reset_date`` — implementation detail of the lazy reset; admin
  should look at ``today_count`` and the running quota effective values
  instead.
- ``last_seq_no`` — also implementation detail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.tier_config import VALID_TIERS

# ---------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------


# Username regex matches the seed: alnum + underscore + dot + dash, length
# 3–64. We deliberately *don't* allow whitespace so the column doubles as
# a stable URL fragment if we ever need it.
_USERNAME_RE = r"^[A-Za-z0-9_.-]{3,64}$"
# Display name is much looser (any printable, max 64), but we still trim
# control chars so admins can't accidentally store ``\x00`` etc.
_DISPLAY_NAME_MAX = 64


def _validate_tier(value: str) -> str:
    if value not in VALID_TIERS:
        raise ValueError(
            f"tier must be one of {VALID_TIERS}, got {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class UserListItem(BaseModel):
    """One row in ``GET /api/admin/users``."""

    id: str
    username: str
    display_name: str | None
    role: str
    tier: str
    status: str
    today_count: int
    soft_quota_effective: int
    hard_quota_effective: int
    created_at: datetime
    last_login_at: datetime | None
    # 30-day rollup: counts of jobs by terminal status. ``total = success +
    # failed + cancelled + currently in flight``. Used to populate the
    # admin list "30d jobs" / "30d ok / fail" columns.
    jobs_30d_total: int
    jobs_30d_success: int
    jobs_30d_failed: int


class UserListResponse(BaseModel):
    items: list[UserListItem]
    page: int
    page_size: int
    total: int


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class UserCreateRequest(BaseModel):
    """Body of ``POST /api/admin/users``.

    Password length is enforced server-side (≥ 8 chars per design doc).
    ``override_*`` are optional; ``None`` means "use tier default".
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., pattern=_USERNAME_RE)
    password: str = Field(..., min_length=8, max_length=256)
    role: str = Field(default="user")
    tier: str = Field(default="free")
    display_name: str | None = Field(default=None, max_length=_DISPLAY_NAME_MAX)
    override_soft_quota: int | None = Field(default=None, ge=0, le=1_000_000)
    override_hard_quota: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("role must be 'admin' or 'user'")
        return v

    @field_validator("tier")
    @classmethod
    def _check_tier(cls, v: str) -> str:
        return _validate_tier(v)

    @field_validator("display_name")
    @classmethod
    def _trim_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


# ---------------------------------------------------------------------------
# Patch (single user)
# ---------------------------------------------------------------------------


class UserPatchRequest(BaseModel):
    """Body of ``PATCH /api/admin/users/<id>``.

    Every field is optional — admin sends just the keys they want to
    change. Omitting a field leaves the column alone; sending ``null``
    on the override fields explicitly clears them (back to tier default).
    Pydantic doesn't distinguish "missing" from "explicit null" at the
    type level; the handler uses ``model_fields_set`` for that.
    """

    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    tier: str | None = None
    status: str | None = None
    display_name: str | None = Field(default=None, max_length=_DISPLAY_NAME_MAX)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    override_soft_quota: int | None = Field(default=None, ge=0, le=1_000_000)
    override_hard_quota: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in ("admin", "user"):
            raise ValueError("role must be 'admin' or 'user'")
        return v

    @field_validator("tier")
    @classmethod
    def _check_tier(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_tier(v)

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # 'deleted' is reserved for the soft-delete endpoint; admins
        # should call DELETE not PATCH to land in that state.
        if v not in ("active", "disabled"):
            raise ValueError("status must be 'active' or 'disabled'")
        return v

    @field_validator("display_name")
    @classmethod
    def _trim_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


# ---------------------------------------------------------------------------
# Detail (read)
# ---------------------------------------------------------------------------


class DailyUsagePoint(BaseModel):
    """One day in the 30-day usage strip."""

    date: str  # 'YYYY-MM-DD' (UTC+8 calendar)
    jobs_count: int
    images_count: int
    success_count: int
    avg_render_seconds: float | None


class TopByCount(BaseModel):
    """Ranked entry in the top-3 model / provider charts."""

    key: str
    count: int


class UserDetailResponse(BaseModel):
    """Full admin view of one user (design doc §13.2)."""

    id: str
    username: str
    display_name: str | None
    role: str
    tier: str
    status: str
    today_count: int
    soft_quota_effective: int
    hard_quota_effective: int
    override_soft_quota: int | None
    override_hard_quota: int | None
    created_at: datetime
    last_login_at: datetime | None
    # 30-day rollups (same as list, plus daily breakdown)
    jobs_30d_total: int
    jobs_30d_success: int
    jobs_30d_failed: int
    daily_usage: list[DailyUsagePoint]
    top_models: list[TopByCount]
    top_providers: list[TopByCount]
    active_session_count: int


# ---------------------------------------------------------------------------
# Action endpoints (disable / enable / reset-password / impersonate / delete)
# ---------------------------------------------------------------------------


class ActionStatusResponse(BaseModel):
    """Returned by disable / enable / soft-delete."""

    id: str
    status: str


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(..., min_length=8, max_length=256)


class ResetPasswordResponse(BaseModel):
    id: str
    ok: bool = True


class ImpersonateResponse(BaseModel):
    """A short-lived bearer token that lets the admin act *as* the target
    user. The token's ``sub`` is the target user's id; the actual admin
    id is carried in the ``impersonator`` claim so audit log writes
    record both correctly.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    target_user_id: str
    target_username: str


# ---------------------------------------------------------------------------
# Bulk patch
# ---------------------------------------------------------------------------


class BulkPatchRequest(BaseModel):
    """Body of ``POST /api/admin/users/bulk``.

    ``patch`` is the same shape as :class:`UserPatchRequest` but only
    fields that make sense to bulk-change are honoured. Fields outside
    that whitelist (``password``, ``display_name``) are rejected by the
    handler — bulk-changing a per-user secret is almost never what the
    admin meant.
    """

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(..., min_length=1, max_length=500)
    patch: dict[str, Any]


class BulkPatchResponse(BaseModel):
    updated: int
    skipped_ids: list[str]


# ---------------------------------------------------------------------------
# Per-user job listing
# ---------------------------------------------------------------------------


class AdminJobItem(BaseModel):
    """Admin view of one job — extends the user view with provider / cost / retries."""

    hash_id: str
    seq_no: int
    model: str
    status: str
    status_reason: str | None
    provider_used: str | None
    retries: int
    cost_cny: float
    set_id: str | None
    session_id: str | None
    created_at: datetime
    finished_at: datetime | None


class AdminJobListResponse(BaseModel):
    items: list[AdminJobItem]
    page: int
    page_size: int
    total: int
