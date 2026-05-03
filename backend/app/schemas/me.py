"""Schemas for the user-facing ``/api/me/*`` settings surface.

Field set lines up with ``UserPreference`` plus the request shapes the
``/settings`` page POSTs / PATCHes. Nothing here exposes tier, quota,
or any other admin-only telemetry — by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class MeProfileUpdate(BaseModel):
    """PATCH /api/me — partial profile update."""

    # ``model_config = forbid`` so unknown keys (e.g. ``tier``) are
    # rejected at the schema layer instead of silently ignored. The
    # frontend gets a clean 422 ``UNKNOWN_FIELD`` instead of a quiet
    # no-op when it tries to PATCH something it shouldn't.
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=50)
    # ``email`` is intentionally typed as a plain str so the route
    # layer can apply its own validation and surface a stable
    # ``INVALID_EMAIL`` code rather than pydantic's generic 422 shape.
    # Pass ``""`` to clear the email.
    email: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class GenerationPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model_id: str | None = None
    default_aspect_ratio: str | None = None
    default_batch_size: int | None = None
    auto_bind_session: bool | None = None
    auto_retry: bool | None = None
    remember_prompt_history: bool | None = None


class NotificationPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    browser_on_complete: bool | None = None
    sound_on_complete: bool | None = None
    sound_volume: int | None = None
    desktop_badge: bool | None = None
    announcements_level: Literal["all", "important", "none"] | None = None


class AppearancePrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Literal["light", "dark", "system"] | None = None
    density: Literal["comfortable", "compact"] | None = None
    sidebar_default: Literal["expanded", "rail"] | None = None


class LocalePrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str | None = None
    timezone: str | None = None
    date_format: Literal["iso", "long", "us"] | None = None


class PrivacyPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hide_prompts_in_screenshot_mode: bool | None = None


class PreferencesUpdate(BaseModel):
    """PATCH /api/me/preferences — deep partial update.

    Each subsection is independently optional, and within a subsection
    every field is independently optional. Unknown top-level keys or
    unknown leaf fields produce ``UNKNOWN_FIELD`` rather than a silent
    no-op.
    """

    model_config = ConfigDict(extra="forbid")

    generation: GenerationPrefs | None = None
    notifications: NotificationPrefs | None = None
    appearance: AppearancePrefs | None = None
    locale: LocalePrefs | None = None
    privacy: PrivacyPrefs | None = None


class GenerationPrefsView(BaseModel):
    default_model_id: str | None
    default_aspect_ratio: str
    default_batch_size: int
    auto_bind_session: bool
    auto_retry: bool
    remember_prompt_history: bool


class NotificationPrefsView(BaseModel):
    browser_on_complete: bool
    sound_on_complete: bool
    sound_volume: int
    desktop_badge: bool
    announcements_level: str


class AppearancePrefsView(BaseModel):
    theme: str
    density: str
    sidebar_default: str


class LocalePrefsView(BaseModel):
    language: str
    timezone: str
    date_format: str


class PrivacyPrefsView(BaseModel):
    hide_prompts_in_screenshot_mode: bool


class PreferencesView(BaseModel):
    """Full-shape preferences response."""

    generation: GenerationPrefsView
    notifications: NotificationPrefsView
    appearance: AppearancePrefsView
    locale: LocalePrefsView
    privacy: PrivacyPrefsView
    updated_at: datetime


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionEntry(BaseModel):
    id: str
    user_agent: str | None
    ip_hint: str | None
    created_at: datetime
    last_active_at: datetime
    is_current: bool


class SessionsResponse(BaseModel):
    current_session_id: str | None
    sessions: list[SessionEntry]


class RevokeOthersResponse(BaseModel):
    revoked_count: int


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


class DeletionRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class DeletionRequestView(BaseModel):
    id: str
    status: str
    requested_at: datetime
    reason: str | None
    resolved_at: datetime | None
    admin_note: str | None
