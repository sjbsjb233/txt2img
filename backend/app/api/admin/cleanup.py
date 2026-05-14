"""Admin endpoints for cleanup (design doc §13.7).

Three routes:

- ``GET /api/admin/cleanup/suggestions`` — fixed bucket list with
  live counts and a disk-usage summary so the admin UI can render
  recommendations without first composing rules.
- ``POST /api/admin/cleanup`` — dry-run or execute. Dry-run returns
  the totals; execute kicks off an async task and returns its id.
- ``GET /api/admin/cleanup/<task_id>`` — poll task progress.

The execution path is intentionally fire-and-forget: the admin
request returns as soon as the task is registered, and progress is
visible via the polling endpoint. Long-running cleanups (many GB)
would otherwise hold an HTTP connection open longer than a typical
reverse proxy permits.

Real-time ``task_deleted`` SSE broadcasting is wired here: the
cleanup runner takes a callback that we bind to ``sse_hub.broadcast_to_user``
so users see their archive entries vanish as the rm -rf walks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from app.deps import CurrentAdmin
from app.domain.cache_keeper import (
    CleanupValidationError,
    estimate_cleanup,
    execute_cleanup,
    get_cleanup_registry,
    list_cleanup_suggestions,
    coerce_cleanup_rules,
)
from app.domain.sse_hub import get_sse_hub
from app.schemas.admin_cleanup import (
    CleanupDryRunResponse,
    CleanupRequest,
    CleanupRule as CleanupRuleSchema,
    CleanupSuggestionListResponse,
    CleanupSuggestionView,
    CleanupTaskView,
)
from app.utils.audit import write_audit
from app.utils.client_ip import client_ip_from
from app.utils.errors import api_error
from app.db.engine import get_session

logger = logging.getLogger("txt2img.admin.cleanup")

router = APIRouter(prefix="/api/admin/cleanup", tags=["admin", "cleanup"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_bytes(n: int) -> str:
    """Render ``n`` bytes as a short human-readable string.

    Kept here because the admin UI binds the human-friendly string
    directly into the suggestion card. Doing it server-side avoids
    locale ambiguity (1.024 vs 1,024).
    """
    if n <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    val = float(n)
    for unit in units:
        if val < 1024 or unit == units[-1]:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} {unit}"
        val /= 1024
    return f"{val:.1f} TB"


async def _disk_usage_summary() -> dict[str, Any]:
    """Pull the latest ``disk_usage`` rollup. Falls back to a fresh walk."""
    from app.db.models import DiskUsage
    from sqlalchemy import select

    async with get_session() as session:
        rows = (await session.execute(select(DiskUsage))).scalars().all()
    by_scope = {r.scope: r for r in rows}
    total = by_scope.get("jobs_total")
    return {
        "data_jobs_bytes": int(total.bytes) if total else 0,
        "data_jobs_bytes_human": _human_bytes(int(total.bytes)) if total else "0 B",
        "job_count": int(total.job_count) if total else 0,
        "refreshed_at": total.refreshed_at.isoformat() if total else None,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/suggestions", response_model=CleanupSuggestionListResponse)
async def get_suggestions(
    _admin: CurrentAdmin,
) -> CleanupSuggestionListResponse:
    """Return the pre-baked buckets the admin UI surfaces on first paint.

    ``exempt_starred`` is always-on for suggestions: design doc §20
    says starred images are preserved across cleanups, and that's the
    UX expectation for the admin landing page.
    """
    suggestions = await list_cleanup_suggestions(exempt_starred=True)
    disk = await _disk_usage_summary()

    items = [
        CleanupSuggestionView(
            period_label=s.period_label,
            rule=CleanupRuleSchema(
                kind=s.rule.kind,
                days=s.rule.days,
                statuses=list(s.rule.statuses),
            ),
            cutoff=s.cutoff,
            job_count=s.job_count,
            image_count=s.image_count,
            disk_bytes=s.disk_bytes,
            disk_human=_human_bytes(s.disk_bytes),
        )
        for s in suggestions
    ]
    return CleanupSuggestionListResponse(
        suggestions=items,
        disk_usage=disk,
        refreshed_at=disk.get("refreshed_at"),
    )


@router.post(
    "",
    response_model=CleanupTaskView | CleanupDryRunResponse,
)
async def run_cleanup(
    body: CleanupRequest,
    admin: CurrentAdmin,
    request: Request,
) -> CleanupTaskView | CleanupDryRunResponse:
    """Dispatch a dry-run estimate or kick off an async cleanup task.

    Validation ordering: pydantic catches type errors, then the
    domain-side ``coerce_cleanup_rules`` checks the cross-field
    invariants (``older_than_days`` requires days, ``status_only``
    requires statuses, no live statuses, etc).
    """
    raw_rules: list[dict] = [r.model_dump(exclude_none=False) for r in body.rules]
    try:
        rules = coerce_cleanup_rules(raw_rules)
    except CleanupValidationError as exc:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            str(exc),
            field=exc.field,
        )

    if body.dry_run:
        estimate = await estimate_cleanup(
            rules, exempt_starred=body.exempt_starred
        )

        # Audit a dry-run too — admins inspect them before committing
        # and we want the chronological trail to include both halves.
        async with get_session() as session:
            await write_audit(
                session,
                actor_user_id=admin.id,
                action="cleanup.dry_run",
                target_kind="cleanup",
                target_id=None,
                payload={
                    "rules": raw_rules,
                    "exempt_starred": body.exempt_starred,
                    "job_count": estimate.job_count,
                    "image_count": estimate.image_count,
                    "disk_bytes": estimate.disk_bytes,
                },
                ip=client_ip_from(request),
            )

        return CleanupDryRunResponse(
            job_count=estimate.job_count,
            image_count=estimate.image_count,
            disk_bytes=estimate.disk_bytes,
            disk_human=_human_bytes(estimate.disk_bytes),
        )

    # Real run — register the task immediately so the response can
    # carry a task_id, then run the work as a background asyncio task.
    sse_hub = get_sse_hub()

    async def _broadcast(user_id: str, hash_id: str) -> None:
        await sse_hub.broadcast_to_user(
            user_id,
            "task_deleted",
            {"hash_id": hash_id},
        )

    # ``execute_cleanup`` registers the state on entry; we await its
    # *registration* phase by running the coroutine until the first
    # ``await`` after registration. Easiest: schedule it as a task and
    # peek at the registry — the call returns synchronously up to the
    # first await inside ``execute_cleanup``, which happens to be the
    # target-collection SELECT, *after* registry.add().
    task_coro = execute_cleanup(
        rules,
        exempt_starred=body.exempt_starred,
        broadcaster=_broadcast,
    )
    task = asyncio.create_task(task_coro)

    # Yield control once so ``execute_cleanup`` runs up to its first
    # await and registers the state. Done in a tight loop with a
    # zero-sleep so we never block on the heavy work.
    for _ in range(8):
        if task.done():
            break
        await asyncio.sleep(0)
        # Find the freshly-registered task by walking the registry.
        recents = get_cleanup_registry().list()
        if recents:
            break

    recents = get_cleanup_registry().list()
    state = recents[0] if recents else None
    if state is None:
        # Should not happen: execute_cleanup registers before its first
        # real await. If it ever fails this fast, surface the error.
        if task.done() and task.exception() is not None:
            raise api_error(
                500,
                "INVALID_PARAMETER",
                f"cleanup failed to start: {task.exception()}",
            )
        raise api_error(500, "INVALID_PARAMETER", "cleanup task missing")

    # Audit row for the kick-off.
    async with get_session() as session:
        await write_audit(
            session,
            actor_user_id=admin.id,
            action="cleanup.execute",
            target_kind="cleanup",
            target_id=state.task_id,
            payload={
                "rules": raw_rules,
                "exempt_starred": body.exempt_starred,
            },
            ip=client_ip_from(request),
        )

    return CleanupTaskView(**state.public())


@router.get("/{task_id}", response_model=CleanupTaskView)
async def get_cleanup_task(
    task_id: str,
    _admin: CurrentAdmin,
) -> CleanupTaskView:
    """Polling endpoint — returns the live state of one cleanup task."""
    state = get_cleanup_registry().get(task_id)
    if state is None:
        raise api_error(
            404,
            "NOT_FOUND",
            f"Cleanup task {task_id!r} not found.",
            field="task_id",
        )
    return CleanupTaskView(**state.public())
