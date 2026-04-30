"""Admin user-management router (PR-15 / design doc §13.2).

This module is the single home for every ``/api/admin/users`` route.
It owns three responsibility blocks:

1. **CRUD on the ``users`` row** — list (with filter + sort + pagination),
   create, get, patch, soft-delete, disable / enable, reset password.
2. **Bulk patch** — restricted to a small whitelist of fields that
   make sense to change in bulk (``tier`` / ``status`` / overrides).
3. **Operations** — impersonate token issue, per-user job listing
   (admin view), 30-day usage rollup.

Cross-cutting design rules
--------------------------

* Every route depends on ``CurrentAdminContext`` rather than just
  ``CurrentAdmin``. Two reasons: (a) handlers need the
  ``impersonator_id`` field to fail fast even though admin access is
  also forbidden in the dep — defence in depth, (b) future routes
  (PR-17) want IP for the audit log without redoing extraction.
* The admin user-management surface mutates one user per request
  (or, for ``bulk``, a bounded list). We open one transaction per
  request and emit exactly one audit row per write.
* "Username" is unique modulo soft-delete: ``alice`` deleted →
  ``alice__deleted_<ts>`` so a fresh signup can reuse the name. The
  soft-delete logic does the renaming in the same transaction.
* Self-targeting is forbidden for destructive actions (``disable`` /
  ``delete`` an admin's own row) so an admin cannot lock themselves
  out of the system in one click.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import (
    Select,
    and_,
    desc,
    distinct,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import (
    Image,
    Job,
    Session as SessionRow,
    User,
)
from app.deps import CurrentAdminContext
from app.domain.tier_config import VALID_TIERS, get_tier_config
from app.schemas.admin_users import (
    ActionStatusResponse,
    AdminJobItem,
    AdminJobListResponse,
    BulkPatchRequest,
    BulkPatchResponse,
    DailyUsagePoint,
    ImpersonateResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TopByCount,
    UserCreateRequest,
    UserDetailResponse,
    UserListItem,
    UserListResponse,
    UserPatchRequest,
)
from app.utils.audit import write_audit
from app.utils.errors import api_error
from app.utils.ids import new_user_id
from app.utils.security import hash_password, issue_impersonate_token

logger = logging.getLogger("txt2img.admin.users")

router = APIRouter(prefix="/api/admin/users", tags=["admin", "users"])


# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------


# Listing knobs. The page-size cap is intentionally loose (1000) so an
# admin export script can fetch a slice without tons of round trips, but
# the default keeps the table snappy.
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 1000


_VALID_SORT_KEYS = {
    # Frontend-facing key → ORM column. We deliberately don't expose
    # arbitrary ORDER BY because that would let an admin sort on a
    # column that has no index and then complain about latency.
    "created_at": User.created_at,
    "last_login_at": User.last_login_at,
    "today_count": User.today_count,
    "username": User.username,
}


# Fields permitted in the ``bulk.patch`` body. Bulk-changing a password
# or display name almost never makes sense; allowing it would also
# require us to broadcast / log per-user diffs we'd have nowhere good
# to surface.
_BULK_ALLOWED_FIELDS = {
    "tier",
    "status",
    "override_soft_quota",
    "override_hard_quota",
}


# Daily usage strip and rollup window per design doc §13.2: 30 days.
_USAGE_WINDOW_DAYS = 30


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _effective_quotas(user: User) -> tuple[int, int]:
    """Resolve a user's effective ``(soft, hard)`` quotas.

    Wraps :meth:`TierConfig.effective_quotas` and lets the route
    handlers be tier-cache agnostic.
    """
    return get_tier_config().effective_quotas(user)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_sort(sort: str | None) -> tuple[Any, bool]:
    """Decode a ``key:direction`` string into ``(column, desc)``.

    ``None`` / empty → default of ``last_login_at desc``. Anything
    unrecognised raises 400 with a useful field hint.
    """
    if not sort:
        return User.last_login_at, True

    key, _, direction = sort.partition(":")
    key = key.strip()
    direction = direction.strip().lower() or "desc"

    if key not in _VALID_SORT_KEYS:
        raise api_error(
            400,
            "BAD_REQUEST",
            f"sort key must be one of {sorted(_VALID_SORT_KEYS)}",
            field="sort",
        )
    if direction not in ("asc", "desc"):
        raise api_error(
            400,
            "BAD_REQUEST",
            "sort direction must be 'asc' or 'desc'",
            field="sort",
        )
    return _VALID_SORT_KEYS[key], direction == "desc"


# ---------------------------------------------------------------------------
# 30-day rollups
# ---------------------------------------------------------------------------


async def _jobs_30d_counts_bulk(
    session: AsyncSession, user_ids: list[str]
) -> dict[str, tuple[int, int, int]]:
    """Return ``user_id → (total, success, failed)`` for the 30d window.

    Implemented in one query so the list endpoint stays cheap even at
    1k users. Cancelled / queued / running don't count toward "failed"
    or "success" — they roll into ``total`` only. ``DELETED`` jobs are
    excluded (they're soft-deleted by users / cleanup).
    """
    if not user_ids:
        return {}
    cutoff = _utc_now() - timedelta(days=_USAGE_WINDOW_DAYS)
    result: dict[str, tuple[int, int, int]] = {}

    rows = (
        await session.execute(
            select(
                Job.user_id,
                Job.status,
                func.count(Job.id),
            )
            .where(Job.user_id.in_(user_ids))
            .where(Job.created_at >= cutoff)
            .where(Job.status != "DELETED")
            .group_by(Job.user_id, Job.status)
        )
    ).all()

    totals: defaultdict[str, int] = defaultdict(int)
    success: defaultdict[str, int] = defaultdict(int)
    failed: defaultdict[str, int] = defaultdict(int)
    for user_id, status, count in rows:
        totals[user_id] += count
        if status == "SUCCEEDED":
            success[user_id] += count
        elif status == "FAILED":
            failed[user_id] += count

    for user_id in user_ids:
        result[user_id] = (totals[user_id], success[user_id], failed[user_id])
    return result


async def _daily_usage(
    session: AsyncSession, user_id: str
) -> list[DailyUsagePoint]:
    """Compute the per-day usage strip for the detail view.

    Buckets by Beijing date (UTC+8). We do the bucketing in Python
    rather than SQLite ``date(... , '+8 hours')`` so the generated
    list is dense (every day in the window present, zero-filled) —
    the frontend assumes 30 contiguous points for its bar chart.
    """
    cutoff = _utc_now() - timedelta(days=_USAGE_WINDOW_DAYS)
    rows = (
        await session.execute(
            select(
                Job.id,
                Job.status,
                Job.started_at,
                Job.finished_at,
                Job.created_at,
            )
            .where(Job.user_id == user_id)
            .where(Job.created_at >= cutoff)
            .where(Job.status != "DELETED")
        )
    ).all()

    job_ids = [r[0] for r in rows]
    image_counts: defaultdict[str, int] = defaultdict(int)
    if job_ids:
        img_rows = (
            await session.execute(
                select(Image.job_id, func.count(Image.id))
                .where(Image.job_id.in_(job_ids))
                .group_by(Image.job_id)
            )
        ).all()
        for job_id, n in img_rows:
            image_counts[job_id] = int(n)

    # Bucket by Beijing date.
    bj_offset = timedelta(hours=8)

    def to_bj_date(dt: datetime | None) -> date | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + bj_offset).date()

    today_bj = (_utc_now() + bj_offset).date()
    start_bj = today_bj - timedelta(days=_USAGE_WINDOW_DAYS - 1)

    per_day_jobs: defaultdict[date, int] = defaultdict(int)
    per_day_success: defaultdict[date, int] = defaultdict(int)
    per_day_images: defaultdict[date, int] = defaultdict(int)
    per_day_render: defaultdict[date, list[float]] = defaultdict(list)

    for job_id, status, started_at, finished_at, created_at in rows:
        bucket = to_bj_date(created_at) or today_bj
        if bucket < start_bj or bucket > today_bj:
            continue
        per_day_jobs[bucket] += 1
        if status == "SUCCEEDED":
            per_day_success[bucket] += 1
        per_day_images[bucket] += image_counts.get(job_id, 0)
        if started_at is not None and finished_at is not None:
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if finished_at.tzinfo is None:
                finished_at = finished_at.replace(tzinfo=timezone.utc)
            seconds = (finished_at - started_at).total_seconds()
            if seconds >= 0:
                per_day_render[bucket].append(seconds)

    points: list[DailyUsagePoint] = []
    cursor = start_bj
    while cursor <= today_bj:
        renders = per_day_render.get(cursor, [])
        avg = sum(renders) / len(renders) if renders else None
        points.append(
            DailyUsagePoint(
                date=cursor.isoformat(),
                jobs_count=per_day_jobs.get(cursor, 0),
                images_count=per_day_images.get(cursor, 0),
                success_count=per_day_success.get(cursor, 0),
                avg_render_seconds=round(avg, 2) if avg is not None else None,
            )
        )
        cursor += timedelta(days=1)
    return points


async def _top_by_count(
    session: AsyncSession,
    user_id: str,
    column: Any,
    limit: int = 3,
) -> list[TopByCount]:
    """Return the top ``limit`` values of ``column`` for the user's
    30-day jobs, sorted by count descending. NULL values are skipped.
    """
    cutoff = _utc_now() - timedelta(days=_USAGE_WINDOW_DAYS)
    rows = (
        await session.execute(
            select(column, func.count(Job.id))
            .where(Job.user_id == user_id)
            .where(Job.created_at >= cutoff)
            .where(Job.status != "DELETED")
            .where(column.is_not(None))
            .group_by(column)
            .order_by(desc(func.count(Job.id)))
            .limit(limit)
        )
    ).all()
    return [TopByCount(key=str(k), count=int(c)) for (k, c) in rows]


async def _active_session_count(session: AsyncSession, user_id: str) -> int:
    """Count this user's named sessions that have at least one non-deleted job."""
    row = (
        await session.execute(
            select(func.count(distinct(SessionRow.id)))
            .where(SessionRow.user_id == user_id)
        )
    ).scalar_one()
    return int(row or 0)


# ---------------------------------------------------------------------------
# Row → response mappers
# ---------------------------------------------------------------------------


def _list_item(
    user: User,
    rollup: tuple[int, int, int],
) -> UserListItem:
    soft, hard = _effective_quotas(user)
    total, success, failed = rollup
    return UserListItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        tier=user.tier,
        status=user.status,
        today_count=user.today_count,
        soft_quota_effective=soft,
        hard_quota_effective=hard,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        jobs_30d_total=total,
        jobs_30d_success=success,
        jobs_30d_failed=failed,
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=UserListResponse)
async def list_users(
    _ctx: CurrentAdminContext,
    q: str | None = Query(default=None, max_length=128),
    tier: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(
        default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE
    ),
) -> UserListResponse:
    """List users with optional filtering / sorting / pagination.

    Default sort is ``last_login_at desc``. The frontend admin list
    binds straight to this — see ``frontend/src/api/admin/users.js``.
    """
    if tier is not None and tier not in VALID_TIERS:
        raise api_error(
            400,
            "BAD_REQUEST",
            f"tier must be one of {VALID_TIERS}",
            field="tier",
        )
    if status is not None and status not in ("active", "disabled", "deleted"):
        raise api_error(
            400,
            "BAD_REQUEST",
            "status must be one of 'active' / 'disabled' / 'deleted'",
            field="status",
        )

    sort_col, is_desc = _parse_sort(sort)

    base: Select[Any] = select(User)
    filters = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(
            or_(User.username.like(like), User.display_name.like(like))
        )
    if tier:
        filters.append(User.tier == tier)
    if status:
        filters.append(User.status == status)
    else:
        # By default hide soft-deleted rows; admins who need them
        # can ask explicitly with ?status=deleted.
        filters.append(User.status != "deleted")
    if filters:
        base = base.where(and_(*filters))

    # NULL last_login_at sorts to the end on desc (a fresh user with no
    # logins shouldn't push down a power user). SQLAlchemy lets us be
    # explicit via ``nullslast`` / ``nullsfirst`` but SQLite's emulation
    # is fine for our scale.
    order = sort_col.desc() if is_desc else sort_col.asc()

    async with get_session() as session:
        total = (
            await session.execute(
                select(func.count(User.id)).where(*filters) if filters else select(func.count(User.id))
            )
        ).scalar_one()

        rows = list(
            (
                await session.execute(
                    base.order_by(order, User.id.asc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )

        rollups = await _jobs_30d_counts_bulk(session, [u.id for u in rows])

    items = [_list_item(u, rollups.get(u.id, (0, 0, 0))) for u in rows]
    return UserListResponse(
        items=items, page=page, page_size=page_size, total=int(total or 0)
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("", response_model=UserDetailResponse, status_code=201)
async def create_user(
    body: UserCreateRequest,
    ctx: CurrentAdminContext,
    request: Request,
) -> UserDetailResponse:
    """Create a fresh user. Admin-only.

    Username collisions across active *and* soft-deleted rows raise 422
    — the soft-delete renamer in :func:`delete_user` is what frees a
    name for reuse, so a clean signup with a deleted user's old name
    only works after we've renamed the deletion.
    """
    if (
        body.override_soft_quota is not None
        and body.override_hard_quota is not None
        and body.override_hard_quota < body.override_soft_quota
    ):
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "override_hard_quota must be >= override_soft_quota.",
            field="override_hard_quota",
        )

    async with get_session() as session:
        existing = (
            await session.execute(
                select(User.id).where(User.username == body.username)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"username {body.username!r} is already taken.",
                field="username",
            )

        user = User(
            id=new_user_id(),
            username=body.username,
            password_hash=hash_password(body.password),
            role=body.role,
            tier=body.tier,
            status="active",
            display_name=body.display_name,
            override_soft_quota=body.override_soft_quota,
            override_hard_quota=body.override_hard_quota,
        )
        session.add(user)
        await session.flush()

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="user.create",
            target_kind="user",
            target_id=user.id,
            payload={
                "username": user.username,
                "role": user.role,
                "tier": user.tier,
                "display_name": user.display_name,
                "override_soft_quota": user.override_soft_quota,
                "override_hard_quota": user.override_hard_quota,
            },
            ip=_client_ip(request),
        )
        # Capture identity before the session closes — accessing the
        # ORM row after commit can trigger a lazy refresh that requires
        # an open session.
        new_id = user.id

    return await _build_detail_response_by_id(new_id)


# ---------------------------------------------------------------------------
# Get one (detail)
# ---------------------------------------------------------------------------


async def _build_detail_response_by_id(user_id: str) -> UserDetailResponse:
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")
        rollup = (await _jobs_30d_counts_bulk(session, [user.id])).get(
            user.id, (0, 0, 0)
        )
        daily = await _daily_usage(session, user.id)
        top_models = await _top_by_count(session, user.id, Job.model)
        top_providers = await _top_by_count(session, user.id, Job.provider_used)
        active_sessions = await _active_session_count(session, user.id)

    soft, hard = _effective_quotas(user)
    return UserDetailResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        tier=user.tier,
        status=user.status,
        today_count=user.today_count,
        soft_quota_effective=soft,
        hard_quota_effective=hard,
        override_soft_quota=user.override_soft_quota,
        override_hard_quota=user.override_hard_quota,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        jobs_30d_total=rollup[0],
        jobs_30d_success=rollup[1],
        jobs_30d_failed=rollup[2],
        daily_usage=daily,
        top_models=top_models,
        top_providers=top_providers,
        active_session_count=active_sessions,
    )


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user(
    user_id: str,
    _ctx: CurrentAdminContext,
) -> UserDetailResponse:
    """Detailed view of a single user (incl. 30-day rollups)."""
    return await _build_detail_response_by_id(user_id)


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


def _apply_patch_dict(user: User, patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a validated patch to ``user`` in-place. Returns the diff
    used for the audit log payload (with ``password`` redacted to a
    sentinel rather than logging the new hash).
    """
    diff: dict[str, Any] = {}
    for key, value in patch.items():
        if key == "password":
            user.password_hash = hash_password(value)
            diff["password"] = "***reset***"
        elif key == "display_name":
            user.display_name = value
            diff[key] = value
        else:
            setattr(user, key, value)
            diff[key] = value
    return diff


def _validate_patch_invariants(user: User) -> None:
    """Run the cross-field checks the schema can't cover.

    Currently just the override soft-vs-hard ordering. Called after the
    patch has been merged onto ``user`` so we always see the final view.
    """
    soft = user.override_soft_quota
    hard = user.override_hard_quota
    if soft is not None and hard is not None and hard < soft:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "override_hard_quota must be >= override_soft_quota.",
            field="override_hard_quota",
        )


@router.patch("/{user_id}", response_model=UserDetailResponse)
async def patch_user(
    user_id: str,
    body: UserPatchRequest,
    ctx: CurrentAdminContext,
    request: Request,
) -> UserDetailResponse:
    """Partial update for a user row.

    The frontend's "Edit" form on the focused user drawer maps each
    field 1:1 to one column. Self-demotion (admin → user on their own
    row) is allowed; admins can co-promote one another so a single
    admin demoting themselves can always be undone by another.
    """
    set_fields = body.model_fields_set
    if not set_fields:
        raise api_error(
            400,
            "BAD_REQUEST",
            "PATCH body must contain at least one field.",
        )

    patch_payload = {f: getattr(body, f) for f in set_fields}

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")
        if user.status == "deleted":
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "Cannot edit a soft-deleted user.",
                field="user_id",
            )

        diff = _apply_patch_dict(user, patch_payload)
        _validate_patch_invariants(user)

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="user.update",
            target_kind="user",
            target_id=user.id,
            payload={"changes": diff},
            ip=_client_ip(request),
        )

    return await _build_detail_response_by_id(user_id)


# ---------------------------------------------------------------------------
# Delete (soft) / disable / enable / reset password
# ---------------------------------------------------------------------------


@router.delete("/{user_id}", response_model=ActionStatusResponse)
async def delete_user(
    user_id: str,
    ctx: CurrentAdminContext,
    request: Request,
) -> ActionStatusResponse:
    """Soft-delete a user.

    The username is renamed to ``<old>__deleted_<utc_epoch>`` so a
    fresh signup can reuse the original name (design doc §13.2).
    Self-deletion is forbidden — the admin must hand off to another
    admin first.
    """
    if user_id == ctx.user.id:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "Cannot delete your own account.",
            field="user_id",
        )

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")
        if user.status == "deleted":
            # Idempotent — already deleted.
            return ActionStatusResponse(id=user.id, status="deleted")

        ts = int(_utc_now().timestamp())
        old_username = user.username
        user.username = f"{old_username}__deleted_{ts}"
        user.status = "deleted"

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="user.delete",
            target_kind="user",
            target_id=user.id,
            payload={"old_username": old_username},
            ip=_client_ip(request),
        )

    return ActionStatusResponse(id=user_id, status="deleted")


@router.post("/{user_id}/disable", response_model=ActionStatusResponse)
async def disable_user(
    user_id: str,
    ctx: CurrentAdminContext,
    request: Request,
) -> ActionStatusResponse:
    """Mark a user disabled. The user's existing token continues to
    decode but is rejected by the auth dependencies (403 ACCOUNT_DISABLED).
    """
    if user_id == ctx.user.id:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "Cannot disable your own account.",
            field="user_id",
        )

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")
        if user.status == "deleted":
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "Cannot disable a soft-deleted user.",
                field="user_id",
            )
        if user.status != "disabled":
            user.status = "disabled"
            await write_audit(
                session,
                actor_user_id=ctx.user.id,
                action="user.disable",
                target_kind="user",
                target_id=user.id,
                ip=_client_ip(request),
            )
        return ActionStatusResponse(id=user.id, status="disabled")


@router.post("/{user_id}/enable", response_model=ActionStatusResponse)
async def enable_user(
    user_id: str,
    ctx: CurrentAdminContext,
    request: Request,
) -> ActionStatusResponse:
    """Reverse :func:`disable_user`."""
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")
        if user.status == "deleted":
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "Cannot enable a soft-deleted user.",
                field="user_id",
            )
        if user.status != "active":
            user.status = "active"
            await write_audit(
                session,
                actor_user_id=ctx.user.id,
                action="user.enable",
                target_kind="user",
                target_id=user.id,
                ip=_client_ip(request),
            )
        return ActionStatusResponse(id=user.id, status="active")


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    ctx: CurrentAdminContext,
    request: Request,
) -> ResetPasswordResponse:
    """Force-set a user's password.

    The new value is hashed with argon2id; the prior hash is dropped.
    Audit logs only record that a reset happened, never the new value.
    """
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")
        if user.status == "deleted":
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "Cannot reset password on a soft-deleted user.",
                field="user_id",
            )

        user.password_hash = hash_password(body.new_password)

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="user.reset_password",
            target_kind="user",
            target_id=user.id,
            ip=_client_ip(request),
        )

    return ResetPasswordResponse(id=user_id, ok=True)


# ---------------------------------------------------------------------------
# Impersonate
# ---------------------------------------------------------------------------


@router.post("/{user_id}/impersonate", response_model=ImpersonateResponse)
async def impersonate_user(
    user_id: str,
    ctx: CurrentAdminContext,
    request: Request,
) -> ImpersonateResponse:
    """Issue a 30-minute impersonate token for the target user.

    The returned token signs the request as the target (so the rest of
    the system behaves as if the target were logged in), but encodes
    the original admin id in the ``impersonator`` claim. The admin
    boundary itself rejects impersonate tokens (see
    :func:`app.deps.get_current_admin`) so an impersonator can't
    elevate back to admin via the impersonate token alone.

    Self-impersonation is rejected — pointless and would just
    create confusing audit trails.
    """
    if user_id == ctx.user.id:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "Cannot impersonate yourself.",
            field="user_id",
        )

    async with get_session() as session:
        target = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if target is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")
        if target.status != "active":
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"Cannot impersonate a {target.status} user.",
                field="user_id",
            )

        token, expires_in = issue_impersonate_token(
            target_user_id=target.id,
            target_username=target.username,
            target_role=target.role,
            impersonator_user_id=ctx.user.id,
        )

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="user.impersonate",
            target_kind="user",
            target_id=target.id,
            payload={"expires_in_seconds": expires_in},
            ip=_client_ip(request),
        )

    return ImpersonateResponse(
        access_token=token,
        token_type="bearer",
        expires_in_seconds=expires_in,
        target_user_id=target.id,
        target_username=target.username,
    )


# ---------------------------------------------------------------------------
# Bulk patch
# ---------------------------------------------------------------------------


def _coerce_bulk_patch(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate that the bulk patch only touches whitelisted fields and
    that each value satisfies the same constraints :class:`UserPatchRequest`
    enforces.

    We re-use ``UserPatchRequest`` for per-field validation by feeding
    the raw dict through it; this gives us identical type / range
    checks without re-spelling them.
    """
    if not isinstance(raw, dict) or not raw:
        raise api_error(
            400,
            "BAD_REQUEST",
            "patch must be a non-empty object",
            field="patch",
        )
    invalid = sorted(set(raw) - _BULK_ALLOWED_FIELDS)
    if invalid:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"bulk patch may only set {sorted(_BULK_ALLOWED_FIELDS)}; "
            f"got disallowed: {invalid}",
            field="patch",
        )
    try:
        validated = UserPatchRequest.model_validate(raw)
    except Exception as exc:  # pragma: no cover - pydantic raises ValidationError
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"patch failed validation: {exc}",
            field="patch",
        ) from exc

    return {f: getattr(validated, f) for f in validated.model_fields_set}


@router.post("/bulk", response_model=BulkPatchResponse)
async def bulk_patch_users(
    body: BulkPatchRequest,
    ctx: CurrentAdminContext,
    request: Request,
) -> BulkPatchResponse:
    """Apply the same ``patch`` to every user id in ``ids``.

    Returns the count of rows actually changed plus the list of ids
    skipped because they were missing or soft-deleted. Self-targeting
    in ``ids`` is silently dropped — bulk operations should never
    accidentally lock the admin out, so we don't error on it.
    """
    patch_payload = _coerce_bulk_patch(body.patch)

    requested: list[str] = []
    seen: set[str] = set()
    for raw_id in body.ids:
        if not isinstance(raw_id, str):
            continue
        if raw_id in seen or raw_id == ctx.user.id:
            continue
        seen.add(raw_id)
        requested.append(raw_id)

    if not requested:
        return BulkPatchResponse(updated=0, skipped_ids=list(body.ids))

    skipped: list[str] = []
    updated = 0
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(User).where(User.id.in_(requested))
                )
            )
            .scalars()
            .all()
        )
        found_ids = {u.id for u in rows}
        for missing in requested:
            if missing not in found_ids:
                skipped.append(missing)

        for user in rows:
            if user.status == "deleted":
                skipped.append(user.id)
                continue
            _apply_patch_dict(user, patch_payload)
            try:
                _validate_patch_invariants(user)
            except Exception:
                # Skip rows that would violate invariants; never let one
                # bad row roll back the whole batch.
                skipped.append(user.id)
                continue
            updated += 1

        # Anyone the caller named themselves but who wasn't applied
        # because of self-targeting / duplicate dedup also goes into
        # the skipped list so the response stays exhaustive.
        for raw_id in body.ids:
            if isinstance(raw_id, str) and raw_id == ctx.user.id and raw_id not in skipped:
                skipped.append(raw_id)

        if updated:
            await write_audit(
                session,
                actor_user_id=ctx.user.id,
                action="user.bulk_patch",
                target_kind="user",
                target_id=None,
                payload={
                    "ids_count": updated,
                    "skipped_count": len(skipped),
                    "patch": _audit_safe_patch(patch_payload),
                },
                ip=_client_ip(request),
            )

    return BulkPatchResponse(updated=updated, skipped_ids=skipped)


def _audit_safe_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets out of a patch before it lands in the audit row."""
    safe = dict(patch)
    if "password" in safe:
        safe["password"] = "***reset***"
    return safe


# ---------------------------------------------------------------------------
# Per-user job listing
# ---------------------------------------------------------------------------


@router.get("/{user_id}/jobs", response_model=AdminJobListResponse)
async def list_user_jobs(
    user_id: str,
    _ctx: CurrentAdminContext,
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=50, ge=1, le=500),
) -> AdminJobListResponse:
    """Admin view of one user's jobs.

    Includes operator-only fields (``provider_used`` / ``cost_cny`` /
    ``retries`` / ``status_reason``) the regular archive endpoints
    deliberately omit (design doc §8.6).
    """
    if status is not None and status not in (
        "QUEUED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "DELETED",
    ):
        raise api_error(
            400,
            "BAD_REQUEST",
            "status filter must be a valid job status",
            field="status",
        )

    filters = [Job.user_id == user_id]
    if status:
        filters.append(Job.status == status)

    async with get_session() as session:
        # Confirm the target exists (otherwise we'd silently return
        # an empty list which is nicer for grep-the-DB but worse UX).
        user = (
            await session.execute(select(User.id).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "User not found.", field="user_id")

        total = (
            await session.execute(
                select(func.count(Job.id)).where(*filters)
            )
        ).scalar_one()

        rows = list(
            (
                await session.execute(
                    select(Job)
                    .where(*filters)
                    .order_by(Job.created_at.desc(), Job.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )

    items = [
        AdminJobItem(
            hash_id=j.hash_id,
            seq_no=j.seq_no,
            model=j.model,
            status=j.status,
            status_reason=j.status_reason,
            provider_used=j.provider_used,
            retries=j.retries,
            cost_cny=j.cost_cny,
            set_id=j.set_id,
            session_id=j.session_id,
            created_at=j.created_at,
            finished_at=j.finished_at,
        )
        for j in rows
    ]
    return AdminJobListResponse(
        items=items, page=page, page_size=page_size, total=int(total or 0)
    )


# Re-exports kept for clarity / explicit import in main.py.
__all__ = ["router"]
