"""Admin endpoints for the runtime config table.

Three routes (design doc §13.5 / §16.6):

- ``GET /api/admin/config`` — full snapshot.
- ``GET /api/admin/config/<key>`` — single key (404 if missing).
- ``PATCH /api/admin/config`` — multi-key update with type/range/
  cross-key validation in ``ConfigCenter``.

The PATCH body is a flat ``{key: value}`` dict so weight changes can
be sent atomically. Splitting weights into multiple PATCH calls would
defeat the "weights sum to 1" invariant — every individual request
would fail validation halfway through. We accept the trade-off that
the body shape doesn't follow JSON-Patch convention.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Request

from app.db.engine import get_session
from app.db.models import Config
from app.deps import CurrentAdmin
from app.domain.config_center import (
    ConfigValidationError,
    KNOWN_CONFIG_KEYS,
    get_config_center,
)
from app.schemas.admin_config import ConfigUpdateResponse
from app.utils.audit import write_audit
from app.utils.errors import api_error

from sqlalchemy import select

logger = logging.getLogger("txt2img.admin.config")

router = APIRouter(prefix="/api/admin", tags=["admin", "config"])


def _client_ip(request: Request) -> str | None:
    """Best-effort IP for audit. Same logic as auth.py to stay consistent."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/config")
async def get_full_config(_admin: CurrentAdmin) -> dict[str, Any]:
    """Return every cached config key.

    We re-read from DB instead of trusting the in-memory cache to
    survive an external edit — admin tools that bypass the API (e.g.
    sqlite CLI in an emergency) should still produce a correct view.
    Cost is one ``SELECT *`` on a tiny table; cheap.
    """
    cc = get_config_center()
    await cc.reload()
    return cc.snapshot()


@router.get("/config/{key:path}")
async def get_single_config_key(
    key: str,
    _admin: CurrentAdmin,
) -> dict[str, Any]:
    """Return one ``{key, value}`` pair, or 404 if missing.

    Uses ``path`` converter for the key segment because real keys
    contain dots (``scheduler.global_max_workers``); without it the
    default ``str`` converter forbids them.
    """
    if key not in KNOWN_CONFIG_KEYS:
        raise api_error(
            404,
            "NOT_FOUND",
            f"Unknown config key {key!r}.",
            field=key,
        )

    async with get_session() as session:
        row = (
            await session.execute(select(Config).where(Config.key == key))
        ).scalar_one_or_none()
    if row is None:
        raise api_error(
            404,
            "NOT_FOUND",
            f"Config key {key!r} not in DB.",
            field=key,
        )
    return {"key": key, "value": json.loads(row.value_json)}


@router.patch("/config", response_model=ConfigUpdateResponse)
async def patch_config(
    admin: CurrentAdmin,
    request: Request,
    body: dict[str, Any] = Body(..., embed=False),
) -> ConfigUpdateResponse:
    """Apply a batch of config updates.

    Validation rules (delegated to ``ConfigCenter.set_many``):
        * Every key must appear in ``KNOWN_CONFIG_KEYS``.
        * Each value must satisfy its declared type/range.
        * Cross-key invariants (weights sum to 1, max ≥ base) are
          checked against the *post-update* merged view.

    On success we write a single ``audit_log`` row whose ``payload``
    contains the accepted updates so PR-17's audit feed can replay
    history. The audit insert and the config upserts share one
    session → either both land or neither does.
    """
    if not isinstance(body, dict) or not body:
        raise api_error(
            400, "BAD_REQUEST", "Body must be a non-empty JSON object."
        )

    cc = get_config_center()
    try:
        accepted = await cc.set_many(body, actor_user_id=admin.id)
    except ConfigValidationError as exc:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            str(exc),
            field=exc.field,
        )

    # Audit row in its own short transaction. We don't merge it into
    # ``set_many`` because that helper deliberately doesn't know about
    # the request context (IP, actor); keeping concerns separate makes
    # ``set_many`` easier to call from internal admin tools later.
    async with get_session() as session:
        await write_audit(
            session,
            actor_user_id=admin.id,
            action="config.update",
            target_kind="config",
            target_id=None,
            payload={"keys": sorted(accepted.keys()), "values": accepted},
            ip=_client_ip(request),
        )

    return ConfigUpdateResponse(updated=accepted)
