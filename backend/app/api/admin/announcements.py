"""Admin endpoints for announcements (PR-17 / design doc §13.7+§11).

Routes:

- ``GET    /api/admin/announcements`` — list (sorted by created_at desc)
- ``POST   /api/admin/announcements`` — create. Two body shapes are
  accepted:
    * ``application/json`` for ``content_kind='text'``
    * ``multipart/form-data`` with a ``payload`` JSON part + a ``cover``
      file part for ``content_kind='image'``
- ``GET    /api/admin/announcements/<id>`` — single
- ``PATCH  /api/admin/announcements/<id>`` — partial update
- ``DELETE /api/admin/announcements/<id>`` — hard delete + cover cleanup

Side-effects on create / update:

- The :func:`schedule_broadcast` call kicks off the SSE fan-out as a
  background task so the response stays snappy even when audience is
  large.
- An ``audit_log`` row is appended in the same transaction as the data
  write — either both land or neither does.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import Announcement, AnnouncementRead
from app.deps import CurrentAdminContext
from app.domain.announcement_bus import (
    estimate_audience_count,
    read_count,
    schedule_broadcast,
)
from app.schemas.announcements import (
    AnnouncementAdminView,
    AnnouncementCreateRequest,
    AnnouncementListResponse,
    AnnouncementPatchRequest,
)
from app.utils.audit import write_audit
from app.utils.client_ip import client_ip_from
from app.utils.errors import api_error
from app.utils.ids import new_announcement_id

logger = logging.getLogger("txt2img.admin.announcements")

router = APIRouter(prefix="/api/admin/announcements", tags=["admin", "announcements"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Hard cap on cover image size — prevents an admin uploading a 100 MB
# JPG that would slow down every page-load thereafter. 10 MB is plenty
# for a single banner image.
_MAX_COVER_BYTES = 10 * 1024 * 1024
_ALLOWED_COVER_MIME = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}


def _ann_dir(ann_id: str) -> Path:
    """Return the on-disk directory for one announcement's assets."""
    settings = get_settings()
    return Path(settings.DATA_ROOT) / "announcements" / ann_id


def _cover_filename_from_mime(mime: str) -> str:
    ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }[mime]
    return f"cover.{ext}"


async def _save_cover(
    ann_id: str, upload: UploadFile
) -> tuple[str, str]:
    """Persist ``upload`` under ``data/announcements/<id>/`` and return
    ``(rel_path, mime)``.

    The rel-path is what we store in the DB ``content`` column; admin
    list / cover endpoint resolve it relative to ``DATA_ROOT``.
    """
    mime = (upload.content_type or "").lower()
    if mime not in _ALLOWED_COVER_MIME:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"cover mime must be one of {sorted(_ALLOWED_COVER_MIME)}; got {mime!r}",
            field="cover",
        )

    body = await upload.read()
    if len(body) > _MAX_COVER_BYTES:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"cover file too large ({len(body)} bytes; max {_MAX_COVER_BYTES})",
            field="cover",
        )
    if len(body) == 0:
        raise api_error(
            422, "INVALID_PARAMETER", "cover upload is empty", field="cover"
        )

    target_dir = _ann_dir(ann_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    fname = _cover_filename_from_mime(mime)
    target = target_dir / fname
    target.write_bytes(body)

    rel = f"announcements/{ann_id}/{fname}"
    return rel, mime


def _delete_cover_dir(ann_id: str) -> None:
    """Remove an announcement's on-disk asset directory if present."""
    d = _ann_dir(ann_id)
    if not d.exists():
        return
    try:
        shutil.rmtree(d)
    except OSError:
        logger.warning("failed to rm announcement dir %s", d, exc_info=True)


def _decode_audience(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, list):
        return None
    return [str(x) for x in decoded if isinstance(x, (str, int))]


async def _to_admin_view(announcement: Announcement) -> AnnouncementAdminView:
    """Map an ORM row to the admin-facing pydantic shape.

    Two follow-up queries enrich the view:
      - ``estimate_audience_count`` — the "REACH" column.
      - ``read_count``               — the "READ" column.

    Both run in their own short transactions so the view assembly is
    safe outside the surrounding session.
    """
    now = datetime.now(timezone.utc)
    starts = announcement.starts_at
    ends = announcement.ends_at
    if starts is not None and starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    if ends is not None and ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    is_live = (
        starts is not None
        and starts <= now
        and (ends is None or ends > now)
    )

    reach = await estimate_audience_count(announcement)
    reads = await read_count(announcement.id)

    return AnnouncementAdminView(
        id=announcement.id,
        title=announcement.title,
        content_kind=announcement.content_kind,  # type: ignore[arg-type]
        content=announcement.content,
        audience_kind=announcement.audience_kind,  # type: ignore[arg-type]
        audience_data=_decode_audience(announcement.audience_data),
        starts_at=announcement.starts_at,
        ends_at=announcement.ends_at,
        dismissable=bool(announcement.dismissable),
        priority=int(announcement.priority),
        created_by=announcement.created_by,
        created_at=announcement.created_at,
        updated_at=announcement.updated_at,
        is_live=is_live,
        reach=reach,
        read_count=reads,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=AnnouncementListResponse)
async def list_announcements(_ctx: CurrentAdminContext) -> AnnouncementListResponse:
    """Return every announcement, newest first.

    No pagination — even at hundreds of announcements the list fits in
    one response and the admin UI sorts/filters client-side. We can
    add pagination later if it ever becomes a problem.
    """
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Announcement).order_by(
                    Announcement.created_at.desc(), Announcement.id.desc()
                )
            )
        ).scalars().all()

    items = [await _to_admin_view(r) for r in rows]
    return AnnouncementListResponse(items=items)


@router.post("", response_model=AnnouncementAdminView, status_code=201)
async def create_announcement(
    request: Request,
    ctx: CurrentAdminContext,
    payload: str | None = Form(default=None),
    cover: UploadFile | None = File(default=None),
) -> AnnouncementAdminView:
    """Create one announcement.

    Two ways to call this:

    1. JSON body (``content_kind='text'``): just POST a JSON request
       matching :class:`AnnouncementCreateRequest`.
    2. Multipart (``content_kind='image'``): send the JSON body in a
       ``payload`` form field plus the cover file in ``cover``. The
       handler stores the cover under ``data/announcements/<id>/`` and
       sets ``content`` to the rel-path so the user-facing cover
       endpoint can serve it.

    The route's signature accepts both styles by reading the request
    content-type — JSON callers leave ``payload`` / ``cover`` as None
    and the body is parsed from ``request.json()`` instead.
    """
    body_dict: dict[str, Any]
    if payload is not None:
        try:
            body_dict = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise api_error(
                400,
                "BAD_REQUEST",
                f"payload form field must be JSON: {exc}",
                field="payload",
            ) from exc
    else:
        # JSON-only path. Reading the body twice (Pydantic also calls
        # ``.json()``) is fine because Starlette caches it.
        try:
            body_dict = await request.json()
        except (ValueError, TypeError) as exc:
            raise api_error(
                400,
                "BAD_REQUEST",
                f"request body must be JSON: {exc}",
            ) from exc

    try:
        body = AnnouncementCreateRequest.model_validate(body_dict)
    except ValidationError as exc:
        raise api_error(
            422, "INVALID_PARAMETER", f"invalid payload: {exc}", field="payload"
        ) from exc

    # Image announcements must have a cover; text announcements must NOT
    # have one (an image-shaped announcement masquerading as text would
    # leak a path the admin didn't intend).
    if body.content_kind == "image" and cover is None:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "image announcements require a cover upload",
            field="cover",
        )
    if body.content_kind == "text" and cover is not None:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "text announcements must not include a cover upload",
            field="cover",
        )

    ann_id = new_announcement_id()
    rel_path: str | None = None
    if body.content_kind == "image":
        assert cover is not None  # narrow for type-checker
        rel_path, _ = await _save_cover(ann_id, cover)

    audience_data_json = (
        json.dumps(body.audience_data, separators=(",", ":"))
        if body.audience_data is not None
        else None
    )

    # Image announcements store the rel-path as ``content`` so the
    # later read paths (admin list + user banner) have one column to
    # consult.
    stored_content = rel_path if body.content_kind == "image" else body.content

    async with get_session() as session:
        ann = Announcement(
            id=ann_id,
            title=body.title,
            content_kind=body.content_kind,
            content=stored_content,
            audience_kind=body.audience_kind,
            audience_data=audience_data_json,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            dismissable=1 if body.dismissable else 0,
            priority=body.priority,
            created_by=ctx.user.id,
        )
        session.add(ann)
        await session.flush()

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="announcement.create",
            target_kind="announcement",
            target_id=ann_id,
            payload={
                "title": ann.title,
                "content_kind": ann.content_kind,
                "audience_kind": ann.audience_kind,
                "audience_data": body.audience_data,
                "priority": ann.priority,
                "dismissable": bool(ann.dismissable),
                "starts_at": (
                    ann.starts_at.isoformat() if ann.starts_at else None
                ),
                "ends_at": ann.ends_at.isoformat() if ann.ends_at else None,
            },
            ip=client_ip_from(request),
        )

    # Re-read after commit so the response carries server-generated
    # fields (created_at / updated_at) at their persisted values.
    async with get_session() as session:
        fresh = (
            await session.execute(
                select(Announcement).where(Announcement.id == ann_id)
            )
        ).scalar_one()
        view = await _to_admin_view(fresh)

    # Fire-and-forget SSE fan-out only if the announcement is live or
    # already in its window. A draft scheduled for the future shouldn't
    # poke users — they'll receive it once the active-list query lands
    # within the window.
    now = datetime.now(timezone.utc)
    starts = fresh.starts_at
    if starts is not None and starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    if starts is not None and starts <= now:
        schedule_broadcast(fresh)

    return view


@router.get("/{ann_id}", response_model=AnnouncementAdminView)
async def get_announcement(
    ann_id: str,
    _ctx: CurrentAdminContext,
) -> AnnouncementAdminView:
    async with get_session() as session:
        row = (
            await session.execute(
                select(Announcement).where(Announcement.id == ann_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise api_error(
            404, "NOT_FOUND", "Announcement not found.", field="ann_id"
        )
    return await _to_admin_view(row)


@router.patch("/{ann_id}", response_model=AnnouncementAdminView)
async def patch_announcement(
    ann_id: str,
    body: AnnouncementPatchRequest,
    ctx: CurrentAdminContext,
    request: Request,
) -> AnnouncementAdminView:
    """Partial update. Audience changes require both fields together."""
    set_fields = body.model_fields_set
    if not set_fields:
        raise api_error(
            400,
            "BAD_REQUEST",
            "PATCH body must contain at least one field.",
        )

    # Cross-field rule: changing audience requires both kind + data so
    # the result is unambiguous.
    if (
        ("audience_kind" in set_fields) ^ ("audience_data" in set_fields)
        and body.audience_kind != "all"
        and body.audience_data is not None
    ):
        # ^ is XOR — only one of the two was supplied. The "all" case
        # explicitly clears audience_data, so we exempt it.
        if "audience_kind" in set_fields and body.audience_kind != "all":
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "changing audience_kind to non-'all' requires audience_data",
                field="audience_data",
            )
        if "audience_data" in set_fields:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "audience_data may only change with audience_kind",
                field="audience_kind",
            )

    async with get_session() as session:
        row = (
            await session.execute(
                select(Announcement).where(Announcement.id == ann_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise api_error(
                404, "NOT_FOUND", "Announcement not found.", field="ann_id"
            )

        diff: dict[str, Any] = {}
        for field in set_fields:
            if field == "audience_data":
                value = body.audience_data
                row.audience_data = (
                    json.dumps(value, separators=(",", ":"))
                    if value is not None
                    else None
                )
                diff["audience_data"] = value
            elif field == "dismissable":
                row.dismissable = 1 if body.dismissable else 0
                diff["dismissable"] = bool(body.dismissable)
            else:
                value = getattr(body, field)
                # Image content_kind announcements treat ``content`` as a
                # rel-path; admins shouldn't edit it through PATCH.
                if (
                    field == "content"
                    and row.content_kind == "image"
                ):
                    raise api_error(
                        422,
                        "INVALID_PARAMETER",
                        "image announcement content is the cover path; re-create to swap",
                        field="content",
                    )
                setattr(row, field, value)
                diff[field] = value if not isinstance(value, datetime) else value.isoformat()

        row.updated_at = datetime.now(timezone.utc)

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="announcement.update",
            target_kind="announcement",
            target_id=ann_id,
            payload={"changes": diff},
            ip=client_ip_from(request),
        )

    # Re-broadcast so newly-eligible users (e.g. tier expansion) see it.
    async with get_session() as session:
        fresh = (
            await session.execute(
                select(Announcement).where(Announcement.id == ann_id)
            )
        ).scalar_one()
        view = await _to_admin_view(fresh)

    now = datetime.now(timezone.utc)
    starts = fresh.starts_at
    if starts is not None and starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    if starts is not None and starts <= now:
        schedule_broadcast(fresh)

    return view


@router.delete("/{ann_id}")
async def delete_announcement(
    ann_id: str,
    ctx: CurrentAdminContext,
    request: Request,
) -> dict[str, Any]:
    """Hard-delete an announcement and its cover assets.

    Also removes ``announcement_reads`` rows so a future reused id
    doesn't inherit stale read state. Real-world reuse is unlikely
    (ids are random), but defensive cleanup is cheap.
    """
    async with get_session() as session:
        row = (
            await session.execute(
                select(Announcement).where(Announcement.id == ann_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise api_error(
                404, "NOT_FOUND", "Announcement not found.", field="ann_id"
            )

        # Delete reads first so the FK-less join below stays clean.
        from sqlalchemy import delete as sql_delete

        await session.execute(
            sql_delete(AnnouncementRead).where(
                AnnouncementRead.announcement_id == ann_id
            )
        )
        await session.execute(
            sql_delete(Announcement).where(Announcement.id == ann_id)
        )

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="announcement.delete",
            target_kind="announcement",
            target_id=ann_id,
            payload={"title": row.title, "content_kind": row.content_kind},
            ip=client_ip_from(request),
        )

    _delete_cover_dir(ann_id)

    return {"id": ann_id, "deleted": True}


# ---------------------------------------------------------------------------
# Cover read (admin convenience — same path as the user route, served
# from the admin router so admins viewing the editor can preview).
# ---------------------------------------------------------------------------


@router.get("/{ann_id}/cover")
async def get_admin_cover(
    ann_id: str,
    _ctx: CurrentAdminContext,
) -> FileResponse:
    """Serve the cover image for an announcement (admin preview).

    Returns 404 if the announcement isn't an image-kind, or if the
    on-disk file is missing (e.g. data dir got nuked between create
    and read).
    """
    async with get_session() as session:
        row = (
            await session.execute(
                select(Announcement).where(Announcement.id == ann_id)
            )
        ).scalar_one_or_none()
    if row is None or row.content_kind != "image":
        raise api_error(
            404, "NOT_FOUND", "Announcement cover not found.", field="ann_id"
        )

    settings = get_settings()
    abs_path = Path(settings.DATA_ROOT) / row.content
    if not abs_path.exists():
        raise api_error(
            404,
            "NOT_FOUND",
            "Cover file is no longer on disk.",
            field="ann_id",
        )
    return FileResponse(
        abs_path, headers={"Cache-Control": "public, max-age=600"}
    )
