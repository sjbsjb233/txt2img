"""``GET / PATCH /api/me/preferences`` — synced settings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import available_timezones

from fastapi import APIRouter
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import UserPreference
from app.deps import CurrentUser
from app.schemas.me import (
    AppearancePrefsView,
    GenerationPrefsView,
    LocalePrefsView,
    NotificationPrefsView,
    PreferencesUpdate,
    PreferencesView,
    PrivacyPrefsView,
)
from app.utils.audit import write_audit
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.me.preferences")

router = APIRouter(prefix="/api/me/preferences", tags=["me"])


_VALID_RATIOS = {"1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4"}
_VALID_BATCH = {1, 2, 4, 8}
_VALID_LANG = {"en", "zh-CN", "ja", "ko"}
_VALID_TIMEZONES = available_timezones()


def _ensure_pref_row(user_id: str) -> UserPreference:
    return UserPreference(user_id=user_id)


def _to_view(row: UserPreference) -> PreferencesView:
    return PreferencesView(
        generation=GenerationPrefsView(
            default_model_id=row.default_model_id,
            default_aspect_ratio=row.default_aspect_ratio,
            default_batch_size=row.default_batch_size,
            auto_bind_session=bool(row.auto_bind_session),
            auto_retry=bool(row.auto_retry),
            remember_prompt_history=bool(row.remember_prompt_history),
        ),
        notifications=NotificationPrefsView(
            browser_on_complete=bool(row.notif_browser_on_complete),
            sound_on_complete=bool(row.notif_sound_on_complete),
            sound_volume=row.notif_sound_volume,
            desktop_badge=bool(row.notif_desktop_badge),
            announcements_level=row.notif_announcements_level,
        ),
        appearance=AppearancePrefsView(
            theme=row.theme,
            density=row.density,
            sidebar_default=row.sidebar_default,
        ),
        locale=LocalePrefsView(
            language=row.language,
            timezone=row.timezone,
            date_format=row.date_format,
        ),
        privacy=PrivacyPrefsView(
            hide_prompts_in_screenshot_mode=bool(
                row.hide_prompts_in_screenshot_mode
            ),
        ),
        updated_at=row.updated_at,
    )


@router.get("", response_model=PreferencesView)
async def get_preferences(user: CurrentUser) -> PreferencesView:
    """Read the user's preferences row, lazily creating a default one.

    Lazy-create means brand new accounts and accounts that pre-date the
    settings rollout both get a sensible row on their first read instead
    of needing a back-fill migration.
    """
    async with get_session() as session:
        row = (
            await session.execute(
                select(UserPreference).where(UserPreference.user_id == user.id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = _ensure_pref_row(user.id)
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return _to_view(row)


def _apply_generation(row: UserPreference, prefs, fields: list[str]) -> None:
    if prefs.default_model_id is not None:
        row.default_model_id = prefs.default_model_id or None
        fields.append("generation.default_model_id")
    elif (
        "default_model_id" in prefs.model_fields_set
    ):  # explicit null clears
        row.default_model_id = None
        fields.append("generation.default_model_id")
    if prefs.default_aspect_ratio is not None:
        if prefs.default_aspect_ratio not in _VALID_RATIOS:
            raise api_error(
                422,
                "INVALID_PREFERENCE",
                "Aspect ratio is not allowed.",
                field="generation.default_aspect_ratio",
            )
        row.default_aspect_ratio = prefs.default_aspect_ratio
        fields.append("generation.default_aspect_ratio")
    if prefs.default_batch_size is not None:
        if prefs.default_batch_size not in _VALID_BATCH:
            raise api_error(
                422,
                "INVALID_PREFERENCE",
                "Batch size must be one of 1, 2, 4, 8.",
                field="generation.default_batch_size",
            )
        row.default_batch_size = prefs.default_batch_size
        fields.append("generation.default_batch_size")
    if prefs.auto_bind_session is not None:
        row.auto_bind_session = 1 if prefs.auto_bind_session else 0
        fields.append("generation.auto_bind_session")
    if prefs.auto_retry is not None:
        row.auto_retry = 1 if prefs.auto_retry else 0
        fields.append("generation.auto_retry")
    if prefs.remember_prompt_history is not None:
        row.remember_prompt_history = 1 if prefs.remember_prompt_history else 0
        fields.append("generation.remember_prompt_history")


def _apply_notifications(row: UserPreference, prefs, fields: list[str]) -> None:
    if prefs.browser_on_complete is not None:
        row.notif_browser_on_complete = 1 if prefs.browser_on_complete else 0
        fields.append("notifications.browser_on_complete")
    if prefs.sound_on_complete is not None:
        row.notif_sound_on_complete = 1 if prefs.sound_on_complete else 0
        fields.append("notifications.sound_on_complete")
    if prefs.sound_volume is not None:
        if not 0 <= prefs.sound_volume <= 100:
            raise api_error(
                422,
                "INVALID_PREFERENCE",
                "Sound volume must be between 0 and 100.",
                field="notifications.sound_volume",
            )
        row.notif_sound_volume = prefs.sound_volume
        fields.append("notifications.sound_volume")
    if prefs.desktop_badge is not None:
        row.notif_desktop_badge = 1 if prefs.desktop_badge else 0
        fields.append("notifications.desktop_badge")
    if prefs.announcements_level is not None:
        row.notif_announcements_level = prefs.announcements_level
        fields.append("notifications.announcements_level")


def _apply_appearance(row: UserPreference, prefs, fields: list[str]) -> None:
    if prefs.theme is not None:
        row.theme = prefs.theme
        fields.append("appearance.theme")
    if prefs.density is not None:
        row.density = prefs.density
        fields.append("appearance.density")
    if prefs.sidebar_default is not None:
        row.sidebar_default = prefs.sidebar_default
        fields.append("appearance.sidebar_default")


def _apply_locale(row: UserPreference, prefs, fields: list[str]) -> None:
    if prefs.language is not None:
        if prefs.language not in _VALID_LANG:
            raise api_error(
                422,
                "INVALID_PREFERENCE",
                "Language is not supported.",
                field="locale.language",
            )
        row.language = prefs.language
        fields.append("locale.language")
    if prefs.timezone is not None:
        if prefs.timezone not in _VALID_TIMEZONES:
            raise api_error(
                422,
                "INVALID_PREFERENCE",
                "Timezone is not a known IANA name.",
                field="locale.timezone",
            )
        row.timezone = prefs.timezone
        fields.append("locale.timezone")
    if prefs.date_format is not None:
        row.date_format = prefs.date_format
        fields.append("locale.date_format")


def _apply_privacy(row: UserPreference, prefs, fields: list[str]) -> None:
    if prefs.hide_prompts_in_screenshot_mode is not None:
        row.hide_prompts_in_screenshot_mode = (
            1 if prefs.hide_prompts_in_screenshot_mode else 0
        )
        fields.append("privacy.hide_prompts_in_screenshot_mode")


@router.patch("", response_model=PreferencesView)
async def patch_preferences(
    body: PreferencesUpdate, user: CurrentUser
) -> PreferencesView:
    fields: list[str] = []

    async with get_session() as session:
        row = (
            await session.execute(
                select(UserPreference).where(UserPreference.user_id == user.id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = _ensure_pref_row(user.id)
            session.add(row)
            await session.flush()

        if body.generation is not None:
            _apply_generation(row, body.generation, fields)
        if body.notifications is not None:
            _apply_notifications(row, body.notifications, fields)
        if body.appearance is not None:
            _apply_appearance(row, body.appearance, fields)
        if body.locale is not None:
            _apply_locale(row, body.locale, fields)
        if body.privacy is not None:
            _apply_privacy(row, body.privacy, fields)

        if fields:
            row.updated_at = datetime.now(timezone.utc)
            await write_audit(
                session,
                actor_user_id=user.id,
                action="me.preferences.update",
                target_kind="user",
                target_id=user.id,
                payload={"fields": fields},
            )

        await session.flush()
        await session.refresh(row)
        return _to_view(row)
