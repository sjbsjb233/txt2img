"""Admin endpoints for the four tier rows (design doc §13.3).

Two routes:

- ``GET /api/admin/tiers`` — all four rows.
- ``PATCH /api/admin/tiers/<tier>`` — partial update of one row, with
  cross-field validation (``hard_quota >= soft_quota``) and immediate
  hot-reload of the in-memory ``TierConfig`` cache.

Tiers are fixed in count (``vip / premium / standard / free``), so
there are no create / delete endpoints — those four rows are seeded
on first boot and stay there forever. Adjusting a tier means PATCHing
one of them, never inserting a fifth.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import Tier as TierRow
from app.deps import CurrentAdmin
from app.domain.tier_config import VALID_TIERS, get_tier_config
from app.schemas.admin_config import (
    TierListResponse,
    TierPatchRequest,
    TierResponse,
)
from app.utils.audit import write_audit
from app.utils.client_ip import client_ip_from
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.admin.tiers")

router = APIRouter(prefix="/api/admin", tags=["admin", "tiers"])


def _row_to_response(row: TierRow) -> TierResponse:
    return TierResponse(
        tier=row.tier,
        weight=row.weight,
        max_concurrency=row.max_concurrency,
        max_queue=row.max_queue,
        soft_quota=row.soft_quota,
        hard_quota=row.hard_quota,
        slo_p95_ms=row.slo_p95_ms,
    )


@router.get("/tiers", response_model=TierListResponse)
async def list_tiers(_admin: CurrentAdmin) -> TierListResponse:
    """Return all tier rows.

    Order is fixed VIP → Premium → Standard → Free so admin UIs don't
    have to sort; this matches the table layout in design doc §3.1.
    """
    async with get_session() as session:
        rows = (await session.execute(select(TierRow))).scalars().all()
    by_tier = {r.tier: r for r in rows}
    ordered = [by_tier[t] for t in VALID_TIERS if t in by_tier]
    return TierListResponse(tiers=[_row_to_response(r) for r in ordered])


@router.patch("/tiers/{tier}", response_model=TierResponse)
async def patch_tier(
    tier: str,
    body: TierPatchRequest,
    admin: CurrentAdmin,
    request: Request,
) -> TierResponse:
    """Partial-update one tier row.

    Cross-field invariants enforced here:

    * ``hard_quota >= soft_quota`` — applied against the *merged* view
      so an admin can ship just one of the two without re-stating the
      other.

    On success the in-memory ``TierConfig`` cache is reloaded *before*
    we return; that way a downstream API call (say, ``GET /api/models``
    in PR-13) sees the new tier params without a window of staleness.
    """
    if tier not in VALID_TIERS:
        raise api_error(
            404,
            "NOT_FOUND",
            f"Unknown tier {tier!r}.",
            field="tier",
        )

    # ``model_fields_set`` distinguishes "field omitted" from "field
    # explicitly set to None"; we honour the latter for slo_p95_ms but
    # nowhere else.
    set_fields = body.model_fields_set
    if not set_fields:
        raise api_error(
            400,
            "BAD_REQUEST",
            "PATCH body must contain at least one field.",
        )

    async with get_session() as session:
        row = (
            await session.execute(select(TierRow).where(TierRow.tier == tier))
        ).scalar_one_or_none()
        if row is None:
            # The seed creates all four; a missing row implies a hand-edited DB.
            raise api_error(
                404, "NOT_FOUND", f"Tier {tier!r} not in DB.", field="tier"
            )

        merged = {
            "weight": row.weight,
            "max_concurrency": row.max_concurrency,
            "max_queue": row.max_queue,
            "soft_quota": row.soft_quota,
            "hard_quota": row.hard_quota,
            "slo_p95_ms": row.slo_p95_ms,
        }
        for field in set_fields:
            merged[field] = getattr(body, field)

        weight = cast(int, merged["weight"])
        max_concurrency = cast(int, merged["max_concurrency"])
        max_queue = cast(int, merged["max_queue"])
        soft_quota = cast(int, merged["soft_quota"])
        hard_quota = cast(int, merged["hard_quota"])
        slo_p95_ms = cast(int | None, merged["slo_p95_ms"])

        if hard_quota < soft_quota:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "hard_quota must be >= soft_quota.",
                field="hard_quota",
            )

        for field, value in merged.items():
            setattr(row, field, value)
        row.updated_at = datetime.now(timezone.utc)

        await write_audit(
            session,
            actor_user_id=admin.id,
            action="tier.update",
            target_kind="tier",
            target_id=tier,
            payload={"changes": {f: merged[f] for f in set_fields}},
            ip=client_ip_from(request),
        )

    # Hot-reload the cache after the transaction commits. ``get_session``
    # commits on clean exit so by the time we get here the row is durable.
    await get_tier_config().reload()

    return TierResponse(
        tier=tier,
        weight=weight,
        max_concurrency=max_concurrency,
        max_queue=max_queue,
        soft_quota=soft_quota,
        hard_quota=hard_quota,
        slo_p95_ms=slo_p95_ms,
    )
