"""Pydantic shapes for ``/api/admin/audit`` (PR-17 / design doc §13.10).

Read-only listing of the ``audit_log`` table. The payload column is
returned verbatim as a parsed dict so the admin UI can pretty-print
without re-parsing JSON strings; payloads we couldn't decode arrive
as ``None``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEntry(BaseModel):
    """One row from the ``audit_log`` table.

    The ``actor`` block resolves the actor's username at request time
    so the admin UI can show ``"alice"`` next to the action without a
    second round-trip. We deliberately don't carry the actor's role
    or other fields — those change over time and stale snapshots in
    the log would confuse the UX.
    """

    id: int
    ts: datetime
    actor_user_id: str
    actor_username: str | None
    action: str
    target_kind: str | None
    target_id: str | None
    payload: dict[str, Any] | None
    ip: str | None


class AuditListResponse(BaseModel):
    items: list[AuditEntry]
    page: int
    page_size: int
    total: int
