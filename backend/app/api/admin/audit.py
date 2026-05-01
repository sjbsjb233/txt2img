"""Admin endpoint for the audit log feed (PR-17 / design doc §13.10).

One route — ``GET /api/admin/audit`` — that returns paginated audit
entries, sorted newest first. Filters: actor (id-or-username),
action (substring or wildcard like ``user.*``), and an inclusive time
window ``[since, until)``.

Username resolution is done once per response by joining against
``users``; we deliberately don't denormalise into ``audit_log`` because
admins occasionally rename users and we want the reading view to
always reflect the current name (a static snapshot would mislead).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, select

from app.db.engine import get_session
from app.db.models import AuditLog, User
from app.deps import CurrentAdmin
from app.schemas.admin_audit import AuditEntry, AuditListResponse
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.admin.audit")

router = APIRouter(prefix="/api/admin/audit", tags=["admin", "audit"])


_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500


def _decode_payload(raw: str | None) -> dict[str, Any] | None:
    """Decode a stored JSON payload back to a dict, or None on failure."""
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(v, dict):
        return v
    # Audit payloads are dicts by convention; surface other shapes
    # under a ``raw`` key so the admin UI sees the data anyway.
    return {"raw": v}


def _action_predicate(action_filter: str):
    """Build a SQLAlchemy predicate for the ``action`` filter string.

    Accepts:
    - ``"user.create"``       — exact match
    - ``"user.*"``            — prefix match (``user.`` and anything after)
    - ``"*.create"``          — suffix match
    - ``"*foo*"``             — substring match

    Anything else is treated as exact for safety.
    """
    s = action_filter.strip()
    if not s:
        return None
    if s.endswith(".*") and not s.startswith("*"):
        return AuditLog.action.like(f"{s[:-1]}%")
    if s.startswith("*.") and not s.endswith("*"):
        return AuditLog.action.like(f"%{s[1:]}")
    if s.startswith("*") and s.endswith("*"):
        return AuditLog.action.like(f"%{s.strip('*')}%")
    return AuditLog.action == s


@router.get("", response_model=AuditListResponse)
async def list_audit(
    _admin: CurrentAdmin,
    actor: str | None = Query(default=None, max_length=128),
    action: str | None = Query(default=None, max_length=128),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
) -> AuditListResponse:
    """Page through audit log rows, newest first.

    The ``actor`` filter accepts either an exact ``u_...`` id or a
    username — the lookup tries id first, then falls back to a
    case-insensitive username search. An unknown actor returns an
    empty page rather than 404 so the UI's filter dropdown can use
    free-form input.

    ``since`` / ``until`` are interpreted in UTC. The frontend sends
    ISO-8601 strings; pydantic decodes them at the route boundary.
    """
    filters = []

    if actor:
        async with get_session() as session:
            # Match the literal value first — typical case is the admin
            # clicked a row's actor link, which carries the id.
            user_row = (
                await session.execute(
                    select(User.id).where(User.id == actor)
                )
            ).scalar_one_or_none()
            if user_row is None:
                # Username fallback — search active + soft-deleted so a
                # disabled / renamed user is still findable.
                user_row = (
                    await session.execute(
                        select(User.id).where(
                            func.lower(User.username) == actor.lower()
                        )
                    )
                ).scalar_one_or_none()
        if user_row is None:
            return AuditListResponse(
                items=[], page=page, page_size=page_size, total=0
            )
        filters.append(AuditLog.actor_user_id == user_row)

    if action:
        pred = _action_predicate(action)
        if pred is not None:
            filters.append(pred)

    if since is not None:
        filters.append(AuditLog.ts >= since)
    if until is not None:
        if since is not None and until <= since:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "until must be > since",
                field="until",
            )
        filters.append(AuditLog.ts < until)

    where_clause = and_(*filters) if filters else None

    async with get_session() as session:
        count_q = select(func.count(AuditLog.id))
        if where_clause is not None:
            count_q = count_q.where(where_clause)
        total = (await session.execute(count_q)).scalar_one()

        list_q = select(AuditLog).order_by(
            AuditLog.ts.desc(), AuditLog.id.desc()
        )
        if where_clause is not None:
            list_q = list_q.where(where_clause)
        list_q = list_q.offset((page - 1) * page_size).limit(page_size)
        rows = (await session.execute(list_q)).scalars().all()

        # Resolve usernames in one query.
        ids_needed = sorted({r.actor_user_id for r in rows})
        username_by_id: dict[str, str] = {}
        if ids_needed:
            user_rows = (
                await session.execute(
                    select(User.id, User.username).where(User.id.in_(ids_needed))
                )
            ).all()
            for uid, uname in user_rows:
                username_by_id[uid] = uname

    items = [
        AuditEntry(
            id=r.id,
            ts=r.ts,
            actor_user_id=r.actor_user_id,
            actor_username=username_by_id.get(r.actor_user_id),
            action=r.action,
            target_kind=r.target_kind,
            target_id=r.target_id,
            payload=_decode_payload(r.payload_json),
            ip=r.ip,
        )
        for r in rows
    ]

    return AuditListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=int(total or 0),
    )
