"""Generation-job endpoints that exist before the full job API lands.

PR-09 intentionally does **not** implement real job creation; PR-13 owns
``POST /api/jobs``. This module keeps one admin-only diagnostic endpoint
so the admission policy can be exercised through FastAPI while the rest
of the task pipeline is still under construction.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import CurrentAdmin
from app.domain.access_policy import get_access_policy

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class DryRunAccessRequest(BaseModel):
    """Minimal payload for the PR-09 access-policy probe."""

    model: str = Field(..., min_length=1, max_length=128)


@router.post("/_dryrun_access")
async def dryrun_access(
    body: DryRunAccessRequest,
    admin: CurrentAdmin,
) -> dict[str, object]:
    """Admin-only probe for the PR-09 access gate.

    Kept out of the user contract by the leading underscore and admin
    guard. PR-13 can remove it once ``POST /api/jobs/precheck`` and
    ``POST /api/jobs`` call ``AccessPolicy`` directly.
    """
    decision = await get_access_policy().evaluate(admin, model=body.model)
    return {
        "passed": decision.passed,
        "soft_quota_exceeded": decision.soft_quota_exceeded,
        "flags": decision.flags,
        "code": decision.code,
        "active_jobs": decision.active_jobs,
        "active_capacity": decision.active_capacity,
    }
