"""``/api/admin/approvals/*`` — admin approval queue.

Today the queue holds account-deletion requests submitted from the
user-facing Danger zone. The shape is deliberately split per kind
(``/deletion``) rather than a polymorphic generic list because
deletion needs admin actions that don't generalise (disabling the
target user, revoking their auth_sessions, etc.).

Approving a deletion request:
  1. flips the AccountDeletionRequest row to ``approved``,
  2. disables the target user,
  3. revokes every active auth_sessions row for that user.

Rejecting:
  1. flips the row to ``rejected`` with an optional admin note.

Both paths emit an audit row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from sqlalchemy import select, update

from app.db.engine import get_session
from app.db.models import (
    AccountDeletionRequest,
    AuthSession,
    User,
)
from app.deps import CurrentAdminContext
from app.domain.account_lifecycle import run_account_lifecycle_sweep
from app.schemas.admin_approvals import (
    ApprovalUser,
    DeletionDecision,
    DeletionRequestList,
    DeletionRequestRow,
)
from app.utils.audit import write_audit
from app.utils.client_ip import client_ip_from
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.admin.approvals")

router = APIRouter(prefix="/api/admin/approvals", tags=["admin", "approvals"])


def _to_user(user: User) -> ApprovalUser:
    return ApprovalUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        tier=user.tier,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _to_row(req: AccountDeletionRequest, user: User) -> DeletionRequestRow:
    return DeletionRequestRow(
        id=req.id,
        user=_to_user(user),
        reason=req.reason,
        requested_at=req.requested_at,
        status=req.status,
        resolved_at=req.resolved_at,
        resolved_by=req.resolved_by,
        admin_note=req.admin_note,
    )


@router.get("/deletion", response_model=DeletionRequestList)
async def list_deletion_requests(
    _ctx: CurrentAdminContext,
    status: str = Query(
        "pending",
        description=(
            "pending / approved / rejected / withdrawn / all. Default 'pending'."
        ),
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> DeletionRequestList:
    if status not in ("pending", "approved", "rejected", "withdrawn", "all"):
        raise api_error(
            400, "BAD_REQUEST", "Invalid status filter.", field="status"
        )

    async with get_session() as session:
        # Total pending — always reported so the admin sidebar badge
        # can show the queue length without a second round trip.
        total_pending = (
            await session.execute(
                select(AccountDeletionRequest.id).where(
                    AccountDeletionRequest.status == "pending"
                )
            )
        ).all()
        total_pending_count = len(total_pending)

        q = (
            select(AccountDeletionRequest, User)
            .join(User, AccountDeletionRequest.user_id == User.id)
            .order_by(AccountDeletionRequest.requested_at.desc())
        )
        if status != "all":
            q = q.where(AccountDeletionRequest.status == status)
        q = q.limit(limit).offset(offset)

        rows = (await session.execute(q)).all()
        items = [_to_row(req, user) for (req, user) in rows]

    return DeletionRequestList(
        items=items, total_pending=total_pending_count
    )


@router.post(
    "/deletion/{request_id}/approve", response_model=DeletionRequestRow
)
async def approve_deletion(
    request_id: str,
    body: DeletionDecision,
    ctx: CurrentAdminContext,
    request: Request,
) -> DeletionRequestRow:
    """Approve a pending deletion request.

    Side effects: disable the target user (so they're signed out
    everywhere on next request), revoke their active auth_sessions,
    and stamp the request row.
    """
    return await _decide(
        request_id=request_id,
        decision="approved",
        admin_note=body.admin_note,
        ctx=ctx,
        request=request,
    )


@router.post(
    "/deletion/{request_id}/reject", response_model=DeletionRequestRow
)
async def reject_deletion(
    request_id: str,
    body: DeletionDecision,
    ctx: CurrentAdminContext,
    request: Request,
) -> DeletionRequestRow:
    """Reject a pending deletion request without disabling the user."""
    return await _decide(
        request_id=request_id,
        decision="rejected",
        admin_note=body.admin_note,
        ctx=ctx,
        request=request,
    )


@router.post("/deletion/sweep")
async def trigger_lifecycle_sweep(
    ctx: CurrentAdminContext, request: Request
) -> dict[str, int]:
    """Run the account-deletion lifecycle sweep on demand.

    The same job runs automatically every 6 h via the lifespan loop;
    this endpoint exists so ops can trigger it manually (e.g. right
    after extending the soft-delete window in policy review) and so
    the e2e test can verify the timeline transitions without waiting
    days. Audit the call so we have a trail of who poked it.
    """
    report = await run_account_lifecycle_sweep()
    async with get_session() as session:
        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="deletion.sweep",
            target_kind="system",
            target_id=None,
            payload=dict(report),
            ip=client_ip_from(request),
        )
    return report


async def _decide(
    *,
    request_id: str,
    decision: str,
    admin_note: str | None,
    ctx: CurrentAdminContext,
    request: Request,
) -> DeletionRequestRow:
    assert decision in ("approved", "rejected")

    async with get_session() as session:
        req = (
            await session.execute(
                select(AccountDeletionRequest).where(
                    AccountDeletionRequest.id == request_id
                )
            )
        ).scalar_one_or_none()
        if req is None:
            raise api_error(404, "NOT_FOUND", "Deletion request not found.")
        if req.status != "pending":
            raise api_error(
                409,
                "DELETION_REQUEST_NOT_PENDING",
                f"Request is already {req.status}.",
            )
        if req.user_id == ctx.user.id:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "Cannot decide on your own deletion request.",
                field="request_id",
            )

        user = (
            await session.execute(
                select(User).where(User.id == req.user_id)
            )
        ).scalar_one_or_none()
        if user is None:
            raise api_error(404, "NOT_FOUND", "Target user not found.")

        now = datetime.now(timezone.utc)
        req.status = decision
        req.resolved_at = now
        req.resolved_by = ctx.user.id
        req.admin_note = admin_note

        if decision == "approved":
            # Approving disables the account and signs every device out.
            # We don't soft-delete here — the soft-delete cleanup job
            # handles the T+7 transition (it sees ``status='disabled'``
            # plus an approved deletion request and renames + flips
            # status='deleted').
            if user.status != "disabled":
                user.status = "disabled"
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id)
                .where(AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action=(
                "deletion.approve" if decision == "approved" else "deletion.reject"
            ),
            target_kind="account_deletion_request",
            target_id=req.id,
            payload={
                "user_id": user.id,
                "has_admin_note": bool(admin_note),
            },
            ip=client_ip_from(request),
        )

        return _to_row(req, user)
