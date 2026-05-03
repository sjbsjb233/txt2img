"""Schemas for the admin "Approvals" surface.

Backs the /api/admin/approvals/* routes. The first kind of approval
shipped is account-deletion requests submitted from the user-facing
Danger zone; the schema is generic enough to grow more kinds (e.g.
tier upgrades) without churn.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApprovalUser(BaseModel):
    """Compact user-summary inlined into approval list rows."""

    id: str
    username: str
    display_name: str | None
    email: str | None
    role: str
    tier: str
    created_at: datetime
    last_login_at: datetime | None


class DeletionRequestRow(BaseModel):
    id: str
    user: ApprovalUser
    reason: str | None
    requested_at: datetime
    status: str
    resolved_at: datetime | None
    resolved_by: str | None
    admin_note: str | None


class DeletionRequestList(BaseModel):
    items: list[DeletionRequestRow]
    total_pending: int


class DeletionDecision(BaseModel):
    """Body for ``POST /api/admin/approvals/deletion/{id}/{decision}``."""

    model_config = ConfigDict(extra="forbid")

    admin_note: str | None = Field(default=None, max_length=500)


class DeletionRequestStatusFilter(BaseModel):
    """Query-string container — used only for documentation."""

    status: Literal["pending", "approved", "rejected", "withdrawn", "all"] = (
        "pending"
    )
