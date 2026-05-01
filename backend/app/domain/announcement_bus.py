"""Announcement fan-out and audience resolution (design doc §11).

This module owns three concerns:

1. **Audience resolution** — given an announcement row, return the list
   of user ids that should see it. Three strategies (``all`` / ``tier``
   / ``user_list``) per §11.3.
2. **SSE fan-out** — once an announcement is created or updated, push an
   ``announcement`` event to every audience member's SSE channel. Done
   in the background so the admin POST returns immediately.
3. **Active-list assembly** — given a user, return every announcement
   that (a) targets them, (b) is within its ``[starts_at, ends_at)``
   window, and (c) the user has not already marked as read.

Why a dedicated module rather than an inline helper in the admin
route: the user-facing ``/api/announcements/active`` endpoint also needs
the audience-resolution logic, and the cleanup keeper (PR-16-style)
will eventually call into it for periodic prune. Keeping the logic
here means the rules about who-sees-what live in exactly one place.

Coupling to the SSE hub is deliberate but lazy: we look up the hub
inside :func:`broadcast_announcement` rather than importing it at
module load so unit tests can stub the hub without monkey-patching
this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import and_, or_, select

from app.db.engine import get_session
from app.db.models import Announcement, AnnouncementRead, User

logger = logging.getLogger("txt2img.announcement_bus")


# ---------------------------------------------------------------------------
# Audience resolution
# ---------------------------------------------------------------------------


def _decode_audience_data(raw: str | None) -> list[str]:
    """Decode the ``audience_data`` column into a list of strings.

    Returns ``[]`` for null / missing / unparseable values — those map to
    "no constraint" only when ``audience_kind == 'all'``; for ``tier`` /
    ``user_list`` the caller should treat empty as "matches nobody".
    """
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("announcement: undecodable audience_data %r", raw[:80])
        return []
    if not isinstance(decoded, list):
        return []
    return [str(x) for x in decoded if isinstance(x, (str, int))]


async def resolve_audience_user_ids(
    announcement: Announcement,
) -> list[str]:
    """Return the list of *active* user ids the announcement targets.

    Soft-deleted and disabled users are excluded — a banner rotting in
    a deactivated user's queue helps nobody, and the SSE hub would
    reject the broadcast anyway.
    """
    kind = announcement.audience_kind
    data = _decode_audience_data(announcement.audience_data)

    async with get_session() as session:
        if kind == "all":
            rows = (
                await session.execute(
                    select(User.id).where(User.status == "active")
                )
            ).all()
        elif kind == "tier":
            if not data:
                return []
            rows = (
                await session.execute(
                    select(User.id)
                    .where(User.status == "active")
                    .where(User.tier.in_(data))
                )
            ).all()
        elif kind == "user_list":
            if not data:
                return []
            rows = (
                await session.execute(
                    select(User.id)
                    .where(User.status == "active")
                    .where(User.id.in_(data))
                )
            ).all()
        else:
            logger.warning("announcement: unknown audience_kind=%r", kind)
            return []

    return [r[0] for r in rows]


async def estimate_audience_count(announcement: Announcement) -> int:
    """Fast count for the admin "reach" column.

    Same query shape as :func:`resolve_audience_user_ids` but only
    returns the count — the admin list view doesn't need the full id
    list and 10k-user installs would otherwise pull 10k rows just to
    render a number.
    """
    from sqlalchemy import func

    kind = announcement.audience_kind
    data = _decode_audience_data(announcement.audience_data)

    async with get_session() as session:
        if kind == "all":
            row = await session.execute(
                select(func.count(User.id)).where(User.status == "active")
            )
        elif kind == "tier":
            if not data:
                return 0
            row = await session.execute(
                select(func.count(User.id))
                .where(User.status == "active")
                .where(User.tier.in_(data))
            )
        elif kind == "user_list":
            if not data:
                return 0
            row = await session.execute(
                select(func.count(User.id))
                .where(User.status == "active")
                .where(User.id.in_(data))
            )
        else:
            return 0
    return int(row.scalar_one() or 0)


async def read_count(announcement_id: str) -> int:
    """Count how many distinct users have marked the announcement read."""
    from sqlalchemy import func

    async with get_session() as session:
        row = await session.execute(
            select(func.count(AnnouncementRead.user_id)).where(
                AnnouncementRead.announcement_id == announcement_id
            )
        )
    return int(row.scalar_one() or 0)


# ---------------------------------------------------------------------------
# Active-for-user query
# ---------------------------------------------------------------------------


def _user_audience_filter(user: User) -> Iterable:
    """SQLAlchemy clauses that match an announcement's audience to ``user``.

    Builds the WHERE fragment used by :func:`active_for_user`. We use
    ``LIKE`` with a JSON-shape probe rather than parsing each row in
    Python — keeps the query in SQL and means the index on the
    announcements table can do its job.

    The probes are intentionally over-broad — for example, the tier
    probe matches the literal ``"vip"`` substring, which would also match
    a username that happened to contain ``"vip"`` *if* announcements
    used a username audience. They don't (audience is tier or user-id),
    so the false-positive surface is empty.
    """
    # The ``audience_data`` column is JSON-encoded; for SQLite there's no
    # JSON1 in our migration baseline, so we fall back to ``LIKE``. The
    # encoded form is e.g. ``["vip","premium"]`` or ``["u_xxx"]``.
    return (
        Announcement.audience_kind == "all",
        and_(
            Announcement.audience_kind == "tier",
            Announcement.audience_data.like(f'%"{user.tier}"%'),
        ),
        and_(
            Announcement.audience_kind == "user_list",
            Announcement.audience_data.like(f'%"{user.id}"%'),
        ),
    )


async def active_for_user(user: User) -> list[Announcement]:
    """Return every announcement the user should currently see.

    Filters:
      - ``starts_at <= now``
      - ``ends_at IS NULL OR ends_at > now``
      - audience matches via :func:`_user_audience_filter`
      - the user hasn't already marked it as read

    Sorted by priority desc, then created_at desc per design doc §11.2.
    """
    now = datetime.now(timezone.utc)
    audience_clauses = _user_audience_filter(user)

    async with get_session() as session:
        # Build the LEFT JOIN against announcement_reads so we can filter
        # out already-read entries in one round trip.
        q = (
            select(Announcement)
            .where(Announcement.starts_at <= now)
            .where(
                or_(
                    Announcement.ends_at.is_(None),
                    Announcement.ends_at > now,
                )
            )
            .where(or_(*audience_clauses))
            .where(
                ~select(AnnouncementRead.user_id)
                .where(AnnouncementRead.announcement_id == Announcement.id)
                .where(AnnouncementRead.user_id == user.id)
                .exists()
            )
            .order_by(Announcement.priority.desc(), Announcement.created_at.desc())
        )
        rows = (await session.execute(q)).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# SSE fan-out
# ---------------------------------------------------------------------------


def _public_user_view(announcement: Announcement) -> dict[str, object]:
    """Build the user-facing payload that travels over SSE.

    Mirrors :class:`AnnouncementUserView` but produces a plain dict so
    the SSE hub doesn't have to import pydantic just to encode.
    """
    cover_url: str | None = None
    if announcement.content_kind == "image":
        cover_url = f"/api/announcements/{announcement.id}/cover"
    return {
        "id": announcement.id,
        "title": announcement.title,
        "content_kind": announcement.content_kind,
        "content": announcement.content,
        "dismissable": bool(announcement.dismissable),
        "priority": int(announcement.priority),
        "starts_at": announcement.starts_at.isoformat()
        if announcement.starts_at is not None
        else None,
        "ends_at": announcement.ends_at.isoformat()
        if announcement.ends_at is not None
        else None,
        "cover_url": cover_url,
    }


async def broadcast_announcement(announcement: Announcement) -> int:
    """Push the announcement to every audience member via the SSE hub.

    Returns the number of users we attempted to push to — useful for
    audit logs. Failures inside individual pushes are swallowed by the
    hub itself; we only care that the *intent* was recorded.

    Called by :func:`publish_announcement` after a successful create
    /update. Calling it directly is fine for tests / re-broadcasts.
    """
    # Lazy import to avoid pulling the SSE hub into the import graph at
    # module load. Tests that patch the hub via
    # ``app.domain.sse_hub.get_sse_hub`` will see the patched version.
    from app.domain.sse_hub import get_sse_hub

    hub = get_sse_hub()
    user_ids = await resolve_audience_user_ids(announcement)
    payload = _public_user_view(announcement)

    pushed = 0
    for uid in user_ids:
        try:
            await hub.broadcast_to_user(uid, "announcement", payload)
            pushed += 1
        except Exception:
            logger.exception(
                "announcement broadcast failed for user=%s ann=%s",
                uid,
                announcement.id,
            )
    logger.info(
        "announcement %s broadcast to %d/%d users",
        announcement.id,
        pushed,
        len(user_ids),
    )
    return pushed


def schedule_broadcast(announcement: Announcement) -> asyncio.Task:
    """Fire-and-forget version of :func:`broadcast_announcement`.

    Use this from admin routes so the HTTP response doesn't wait for the
    fan-out — large user lists would otherwise inflate p95 latency. The
    returned task is also captured by the lifespan tracker so we don't
    leak orphaned tasks at shutdown.
    """

    async def _runner() -> None:
        try:
            await broadcast_announcement(announcement)
        except Exception:
            logger.exception(
                "scheduled announcement broadcast crashed ann=%s",
                announcement.id,
            )

    return asyncio.create_task(_runner())
