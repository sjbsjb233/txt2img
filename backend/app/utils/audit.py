"""Audit log helper.

The ``audit_log`` table records every admin write so PR-17 can show a
chronological feed. PR-04 only ships the *write* path — the list /
filter endpoint arrives with the rest of the admin metrics surface.

Why a dedicated helper instead of inline ``session.add(AuditLog(...))``:

- Centralises the action vocabulary so every admin module spells
  things the same way (``"config.update"`` vs ``"config_update"`` vs
  ``"update_config"`` will rot quickly without a chokepoint).
- One place to attach IP extraction & payload redaction in PR-17.
- Forces every caller to pass ``session`` explicitly so we never
  accidentally open a second transaction inside an admin handler that
  was meant to be atomic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

logger = logging.getLogger("txt2img.audit")


async def write_audit(
    session: AsyncSession,
    *,
    actor_user_id: str,
    action: str,
    target_kind: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Append one row to ``audit_log`` inside the caller's transaction.

    The session is *not* committed here — the caller controls the
    transaction boundary so the audit row lands atomically with whatever
    write triggered it. If the surrounding transaction rolls back, the
    audit row rolls back too, which is exactly the behaviour we want.
    """
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            payload_json=json.dumps(payload, separators=(",", ":"))
            if payload is not None
            else None,
            ip=ip,
        )
    )
    logger.info(
        "audit: actor=%s action=%s target=%s/%s",
        actor_user_id,
        action,
        target_kind,
        target_id,
    )
