"""Helpers for creating jobs without going through the executor."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.engine import get_session
from app.db.models import Job
from app.utils.ids import new_job_hash_id, new_job_internal_id


async def insert_terminal_job(
    user_id: str,
    *,
    tier: str = "premium",
    status: str = "SUCCEEDED",
    model: str = "gpt-image-2",
    seq_no: int = 1,
    created_at: datetime | None = None,
    set_id: str | None = None,
    flags: dict | None = None,
) -> str:
    """Insert one terminal-state job. Returns hash_id."""
    import json

    hid = new_job_hash_id()
    now = created_at or datetime.now(timezone.utc)
    async with get_session() as s:
        s.add(
            Job(
                id=new_job_internal_id(),
                hash_id=hid,
                user_id=user_id,
                tier_at_submit=tier,
                seq_no=seq_no,
                set_id=set_id,
                model=model,
                params_json="{}",
                flags_json=json.dumps(flags or {}),
                status=status,
                created_at=now,
                finished_at=now,
            )
        )
    return hid
