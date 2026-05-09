"""§10 of the testing doc — watchdog auto-finalisation."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import update

from tests._batch_helpers import (
    auth,
    fetch_batch_row,
    flush_emitter,
    install_sse_capture,
    insert_fake_job,
    login_user,
    register_batch,
    slot_dict,
    time_travel_batch_activity,
)


async def _user_id_of(username: str = "alice") -> str:
    from sqlalchemy import select

    from app.db.engine import get_session
    from app.db.models import User

    async with get_session() as session:
        u = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one()
    return u.id


async def _set_submitted_count(batch_id: str, n: int) -> None:
    from app.db.engine import get_session
    from app.db.models import Batch

    async with get_session() as session:
        await session.execute(
            update(Batch).where(Batch.id == batch_id).values(submitted_count=n)
        )


async def _force_status(batch_id: str, status: str) -> None:
    from datetime import datetime, timezone

    from app.db.engine import get_session
    from app.db.models import Batch

    async with get_session() as session:
        values = {"status": status}
        if status in ("completed", "partial", "cancelled", "abandoned"):
            values["finalized_at"] = datetime.now(timezone.utc)
        await session.execute(
            update(Batch).where(Batch.id == batch_id).values(**values)
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_marks_stale_submitting(seeded_app):
    """Doc §10.test_watchdog_marks_stale_submitting."""
    from app.domain.batch_service import run_watchdog_once

    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=12)]
    )
    await _set_submitted_count(batch["batch_id"], 2)
    await time_travel_batch_activity(batch["batch_id"], seconds_into_past=120)
    touched = await run_watchdog_once()
    assert touched >= 1
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "abandoned"
    assert row.finalized_at is not None


@pytest.mark.asyncio
async def test_watchdog_skips_recent_activity(seeded_app):
    """Doc §10.test_watchdog_skips_recent_activity."""
    from app.domain.batch_service import run_watchdog_once

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    # last_activity_at is fresh (just now); watchdog should skip.
    touched = await run_watchdog_once()
    assert touched == 0
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "submitting"


@pytest.mark.asyncio
async def test_watchdog_skips_full_submission(seeded_app):
    """Doc §10.test_watchdog_skips_full_submission — promotes instead of abandoning."""
    from app.domain.batch_service import run_watchdog_once

    token = await login_user(seeded_app)
    user_id = await _user_id_of()
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=4)]
    )
    for _ in range(4):
        await insert_fake_job(user_id=user_id, batch_id=batch["batch_id"])
    await _set_submitted_count(batch["batch_id"], 4)
    await time_travel_batch_activity(batch["batch_id"], seconds_into_past=120)
    await run_watchdog_once()
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "running"


@pytest.mark.asyncio
async def test_watchdog_skips_full_with_terminal_jobs(seeded_app):
    """Doc §10.test_watchdog_skips_full_with_terminal_jobs."""
    from app.domain.batch_service import run_watchdog_once
    from tests._batch_helpers import force_job_status

    token = await login_user(seeded_app)
    user_id = await _user_id_of()
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=4)]
    )
    hashes = [
        await insert_fake_job(user_id=user_id, batch_id=batch["batch_id"])
        for _ in range(4)
    ]
    await _set_submitted_count(batch["batch_id"], 4)
    for h in hashes:
        await force_job_status(h, "SUCCEEDED")
    await flush_emitter()
    await time_travel_batch_activity(batch["batch_id"], seconds_into_past=120)
    await run_watchdog_once()
    row = await fetch_batch_row(batch["batch_id"])
    # Either the hook already finalised it to completed, or the watchdog
    # did. Either way — must end in completed.
    assert row.status == "completed"


@pytest.mark.asyncio
async def test_watchdog_does_not_touch_terminal(seeded_app):
    """Doc §10.test_watchdog_does_not_touch_terminal."""
    from app.domain.batch_service import run_watchdog_once

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    await _force_status(batch["batch_id"], "completed")
    await time_travel_batch_activity(batch["batch_id"], seconds_into_past=600)
    await run_watchdog_once()
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "completed"


@pytest.mark.asyncio
async def test_watchdog_race_with_finalize(seeded_app):
    """Doc §10.test_watchdog_race_with_finalize — only one wins; state is consistent."""
    from app.domain.batch_service import run_watchdog_once

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    await time_travel_batch_activity(batch["batch_id"], seconds_into_past=120)

    finalize_coro = seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(token),
    )
    watchdog_coro = run_watchdog_once()
    finalize_resp, _ = await asyncio.gather(finalize_coro, watchdog_coro)
    # finalize either wins (200) or loses to abandoned (409).
    assert finalize_resp.status_code in (200, 409)
    row = await fetch_batch_row(batch["batch_id"])
    # Whichever wins, the row must be in a single terminal/non-terminal state.
    assert row.status in (
        "running",
        "abandoned",
        "completed",
        "partial",
        "cancelled",
    )


@pytest.mark.asyncio
async def test_watchdog_disabled_by_config(seeded_app, monkeypatch):
    """Doc §10.test_watchdog_disabled_by_config."""
    from app.config import get_settings
    from app.domain.batch_service import run_watchdog_once

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    await time_travel_batch_activity(batch["batch_id"], seconds_into_past=120)
    settings = get_settings()
    monkeypatch.setattr(settings, "BATCH_WATCHDOG_ENABLED", False)
    touched = await run_watchdog_once()
    assert touched == 0
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "submitting"


@pytest.mark.asyncio
async def test_watchdog_isolates_users(seeded_app):
    """Doc §10.test_watchdog_isolates_users."""
    from app.domain.batch_service import run_watchdog_once

    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    a_batch = await register_batch(seeded_app, alice)
    b_batch = await register_batch(seeded_app, bob)
    # Only A's is stale.
    await time_travel_batch_activity(a_batch["batch_id"], seconds_into_past=120)
    await run_watchdog_once()
    a_row = await fetch_batch_row(a_batch["batch_id"])
    b_row = await fetch_batch_row(b_batch["batch_id"])
    assert a_row.status == "abandoned"
    assert b_row.status == "submitting"


@pytest.mark.asyncio
async def test_watchdog_emits_sse_on_abandon(seeded_app):
    """Doc §10.test_watchdog_emits_sse_on_abandon."""
    from app.domain.batch_service import run_watchdog_once

    captured = install_sse_capture()
    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    await time_travel_batch_activity(batch["batch_id"], seconds_into_past=120)
    await run_watchdog_once()
    await flush_emitter()
    bp = [
        c
        for c in captured
        if c.kind == "batch_progress"
        and c.payload["batch_id"] == batch["batch_id"]
        and c.payload["status"] == "abandoned"
    ]
    assert bp, "expected abandoned batch_progress event"
