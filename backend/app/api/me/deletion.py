"""Account deletion request endpoints under ``/api/me/deletion-request``.

Submitting a request inserts one row in ``account_deletion_requests``
with ``status='pending'``; admins approve / reject from the admin
console (out of scope for this PR). Users can withdraw a pending
request which moves it to ``withdrawn`` so the partial-unique index
on pending rows lets them submit a fresh one if they change their
mind again.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import AccountDeletionRequest
from app.deps import CurrentUser
from app.schemas.me import DeletionRequestCreate, DeletionRequestView
from app.utils.audit import write_audit
from app.utils.errors import api_error
from app.utils.ids import new_deletion_request_id

logger = logging.getLogger("txt2img.me.deletion")

router = APIRouter(prefix="/api/me/deletion-request", tags=["me"])


def _to_view(row: AccountDeletionRequest) -> DeletionRequestView:
    return DeletionRequestView(
        id=row.id,
        status=row.status,
        requested_at=row.requested_at,
        reason=row.reason,
        resolved_at=row.resolved_at,
        admin_note=row.admin_note,
    )


@router.get("", response_model=DeletionRequestView | None)
async def get_deletion_request(user: CurrentUser) -> DeletionRequestView | None:
    """Return the user's most-recent deletion request, or null.

    Used by the Settings page to render the danger zone in either
    "submit a request" or "you have a pending request" mode.
    """
    async with get_session() as session:
        row = (
            await session.execute(
                select(AccountDeletionRequest)
                .where(AccountDeletionRequest.user_id == user.id)
                .order_by(AccountDeletionRequest.requested_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return _to_view(row)


@router.post("", response_model=DeletionRequestView, status_code=201)
async def create_deletion_request(
    body: DeletionRequestCreate, user: CurrentUser
) -> DeletionRequestView:
    async with get_session() as session:
        existing = (
            await session.execute(
                select(AccountDeletionRequest)
                .where(AccountDeletionRequest.user_id == user.id)
                .where(AccountDeletionRequest.status == "pending")
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise api_error(
                409,
                "DELETION_REQUEST_PENDING",
                "You already have a pending deletion request.",
            )

        row = AccountDeletionRequest(
            id=new_deletion_request_id(),
            user_id=user.id,
            reason=(body.reason or None),
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)

        await write_audit(
            session,
            actor_user_id=user.id,
            action="me.deletion.request",
            target_kind="user",
            target_id=user.id,
            payload={"has_reason": bool(body.reason)},
        )

        return _to_view(row)


@router.delete("", status_code=204)
async def withdraw_deletion_request(user: CurrentUser) -> Response:
    async with get_session() as session:
        row = (
            await session.execute(
                select(AccountDeletionRequest)
                .where(AccountDeletionRequest.user_id == user.id)
                .where(AccountDeletionRequest.status == "pending")
            )
        ).scalar_one_or_none()
        if row is None:
            raise api_error(
                404,
                "NOT_FOUND",
                "No pending deletion request to withdraw.",
            )
        row.status = "withdrawn"
        row.resolved_at = datetime.now(timezone.utc)

        await write_audit(
            session,
            actor_user_id=user.id,
            action="me.deletion.withdraw",
            target_kind="user",
            target_id=user.id,
        )
    return Response(status_code=204)
