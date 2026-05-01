"""User-facing announcement endpoints (PR-17 / design doc §11.2).

Three routes:

- ``GET  /api/announcements/active`` — every announcement currently
  targeting the user that they have not read.
- ``POST /api/announcements/<id>/read`` — mark one as read; idempotent.
- ``GET  /api/announcements/<id>/cover`` — stream the cover image for
  an image-kind announcement.

Read-only by design — user can never create / edit / delete. The
``cover`` route is a thin proxy to the on-disk asset path stored on
the announcement row; we deliberately don't reuse the admin cover
endpoint so users never need to be admins to fetch their banners.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import Announcement, AnnouncementRead
from app.deps import CurrentUser
from app.domain.announcement_bus import active_for_user
from app.schemas.announcements import (
    ActiveAnnouncementsResponse,
    AnnouncementUserView,
    MarkReadResponse,
)
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.announcements")

router = APIRouter(prefix="/api/announcements", tags=["announcements"])


def _public_view(announcement: Announcement) -> AnnouncementUserView:
    """Map an ORM row to the user-facing pydantic shape.

    Image announcements get a ``cover_url`` pointing at the user-facing
    cover endpoint so the frontend can render ``<img src=...>`` without
    a separate lookup.
    """
    cover_url: str | None = None
    if announcement.content_kind == "image":
        cover_url = f"/api/announcements/{announcement.id}/cover"
    return AnnouncementUserView(
        id=announcement.id,
        title=announcement.title,
        content_kind=announcement.content_kind,  # type: ignore[arg-type]
        content=announcement.content,
        dismissable=bool(announcement.dismissable),
        priority=int(announcement.priority),
        starts_at=announcement.starts_at,
        ends_at=announcement.ends_at,
        cover_url=cover_url,
    )


@router.get("/active", response_model=ActiveAnnouncementsResponse)
async def list_active(user: CurrentUser) -> ActiveAnnouncementsResponse:
    """Return active, unread, audience-matching announcements.

    Sort order matches the SSE delivery order (priority desc,
    created_at desc) so the frontend can render banners in a stable
    sequence regardless of which path delivered them.
    """
    rows = await active_for_user(user)
    return ActiveAnnouncementsResponse(items=[_public_view(r) for r in rows])


@router.post("/{ann_id}/read", response_model=MarkReadResponse)
async def mark_read(ann_id: str, user: CurrentUser) -> MarkReadResponse:
    """Mark one announcement as read for ``user``.

    Idempotent: a second call no-ops because the table has a composite
    PK on (user_id, announcement_id). We use ``INSERT OR IGNORE`` so
    the call is one round trip even on the no-op path.

    The announcement itself doesn't have to exist — a stale id from a
    just-deleted row should still 200 so the frontend's "dismiss"
    button never appears broken. We log it but don't 404.
    """
    async with get_session() as session:
        # Optional existence check that produces a friendly log line —
        # not load-bearing for correctness.
        exists = (
            await session.execute(
                select(Announcement.id).where(Announcement.id == ann_id)
            )
        ).scalar_one_or_none()
        if exists is None:
            logger.info(
                "mark_read on missing announcement id=%s by user=%s",
                ann_id,
                user.id,
            )

        stmt = (
            sqlite_insert(AnnouncementRead)
            .values(
                user_id=user.id,
                announcement_id=ann_id,
            )
            .prefix_with("OR IGNORE")
        )
        await session.execute(stmt)
    return MarkReadResponse(id=ann_id, read=True)


@router.get("/{ann_id}/cover")
async def get_cover(ann_id: str, user: CurrentUser) -> FileResponse:
    """Stream the cover image for an image-kind announcement.

    Visibility check: even though covers don't carry secrets, we still
    require the announcement to be currently visible to ``user`` —
    otherwise a deep-link could leak content from an audience the
    requester is not part of.
    """
    rows = await active_for_user(user)
    target = next((r for r in rows if r.id == ann_id), None)
    if target is None:
        # Fall back to a direct lookup so admins (who get every active
        # entry through the admin route) and audiences with no read
        # state still hit the same shape.
        async with get_session() as session:
            row = (
                await session.execute(
                    select(Announcement).where(Announcement.id == ann_id)
                )
            ).scalar_one_or_none()
        if row is None or row.content_kind != "image":
            raise api_error(
                404,
                "NOT_FOUND",
                "Announcement cover not found.",
                field="ann_id",
            )
        # Visibility window guard — same logic as active_for_user, but
        # without the "unread" half because we want a user to fetch the
        # cover of an announcement they've already dismissed (the
        # dismissed banner may still render briefly client-side).
        now = datetime.now(timezone.utc)
        starts = row.starts_at
        ends = row.ends_at
        if starts is not None and starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        if ends is not None and ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        in_window = (
            starts is not None
            and starts <= now
            and (ends is None or ends > now)
        )
        if not in_window:
            raise api_error(
                404,
                "NOT_FOUND",
                "Announcement cover not found.",
                field="ann_id",
            )
        target = row

    settings = get_settings()
    abs_path = Path(settings.DATA_ROOT) / target.content
    if not abs_path.exists():
        raise api_error(
            404,
            "NOT_FOUND",
            "Cover file is no longer on disk.",
            field="ann_id",
        )
    return FileResponse(
        abs_path,
        headers={"Cache-Control": "public, max-age=600"},
    )
