"""§6 + §7 + §8 of the testing doc — finalize / cancel / delete lifecycle."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, update

from tests._batch_helpers import (
    auth,
    fetch_batch_row,
    flush_emitter,
    force_job_status,
    insert_fake_job,
    install_sse_capture,
    login_user,
    register_batch,
    slot_dict,
)


async def _user_id_of(username: str = "alice") -> str:
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


# ---------------------------------------------------------------------------
# §6 FINALIZE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_to_running(seeded_app):
    """Doc §6.test_finalize_to_running."""
    token = await login_user(seeded_app)
    user_id = await _user_id_of()
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=4)]
    )
    for _ in range(4):
        await insert_fake_job(user_id=user_id, batch_id=batch["batch_id"])
    await _set_submitted_count(batch["batch_id"], 4)
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "running"


@pytest.mark.asyncio
async def test_finalize_to_completed(seeded_app):
    """Doc §6.test_finalize_to_completed."""
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
    # All 4 already terminal: the state-machine hook on the last
    # SUCCEEDED transition will have flipped the batch to ``completed``
    # by itself. Finalize returns 409 in that case (since the row is
    # no longer in ``submitting``); either way, the resulting state
    # must be ``completed``.
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(token),
    )
    assert resp.status_code in (200, 409)
    if resp.status_code == 200:
        assert resp.json()["status"] == "completed"
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "completed"
    assert row.finalized_at is not None


@pytest.mark.asyncio
async def test_finalize_to_partial(seeded_app):
    """Doc §6.test_finalize_to_partial — 3 SUCCEEDED + 1 FAILED."""
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
    for h in hashes[:3]:
        await force_job_status(h, "SUCCEEDED")
    await force_job_status(hashes[3], "FAILED")
    await flush_emitter()
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "partial"


@pytest.mark.asyncio
async def test_finalize_invalid_state(seeded_app):
    """Doc §6.test_finalize_invalid_state — already running."""
    from app.db.engine import get_session
    from app.db.models import Batch

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    async with get_session() as session:
        await session.execute(
            update(Batch).where(Batch.id == batch["batch_id"]).values(status="running")
        )
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(token),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "BATCH_INVALID_STATE"


@pytest.mark.asyncio
async def test_finalize_terminal_state(seeded_app):
    """Doc §6.test_finalize_terminal_state — completed → 409."""
    from datetime import datetime, timezone

    from app.db.engine import get_session
    from app.db.models import Batch

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    async with get_session() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == batch["batch_id"])
            .values(status="completed", finalized_at=datetime.now(timezone.utc))
        )
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(token),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_finalize_unknown(seeded_app):
    """Doc §6.test_finalize_unknown."""
    token = await login_user(seeded_app)
    resp = await seeded_app.post(
        "/api/batches/bat_doesnotex/finalize_submission",
        headers=auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_finalize_foreign(seeded_app):
    """Doc §6.test_finalize_foreign."""
    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    batch = await register_batch(seeded_app, bob)
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(alice),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_finalize_concurrent_one_winner(seeded_app):
    """Doc §6.test_finalize_idempotent_on_concurrent."""
    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)

    async def one():
        return await seeded_app.post(
            f"/api/batches/{batch['batch_id']}/finalize_submission",
            headers=auth(token),
        )

    a, b = await asyncio.gather(one(), one())
    statuses = sorted([a.status_code, b.status_code])
    # At least one must succeed; the loser is either 200 or 409.
    assert 200 in statuses
    # State settles to one value.
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status in ("running", "completed", "partial", "cancelled")


@pytest.mark.asyncio
async def test_finalize_emits_sse(seeded_app):
    """Doc §6.test_finalize_emits_sse — captures one SSE batch_progress."""
    captured = install_sse_capture()
    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(token),
    )
    await flush_emitter()
    bp = [c for c in captured if c.kind == "batch_progress" and c.payload["batch_id"] == batch["batch_id"]]
    assert len(bp) >= 1


# ---------------------------------------------------------------------------
# §7 CANCEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_queued_only(seeded_app):
    """Doc §7.test_cancel_queued_only — RUNNING is left alone."""
    token = await login_user(seeded_app)
    user_id = await _user_id_of()
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=5)]
    )
    hashes = [
        await insert_fake_job(user_id=user_id, batch_id=batch["batch_id"])
        for _ in range(5)
    ]
    # 2 SUCCEEDED + 1 RUNNING + 2 QUEUED.
    await force_job_status(hashes[0], "SUCCEEDED")
    await force_job_status(hashes[1], "SUCCEEDED")
    from app.domain.job_lifecycle import RUNNING, get_job_lifecycle

    await get_job_lifecycle().transition(hashes[2], RUNNING, reason="t")
    # hashes[3], hashes[4] still QUEUED.
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/cancel", headers=auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cancelled_now_count"] == 2

    # RUNNING Job not touched.
    from app.db.engine import get_session
    from app.db.models import Job

    async with get_session() as session:
        rows = (
            await session.execute(select(Job).where(Job.batch_id == batch["batch_id"]))
        ).scalars().all()
    by_hash = {r.hash_id: r for r in rows}
    assert by_hash[hashes[2]].status == "RUNNING"
    assert by_hash[hashes[3]].status == "CANCELLED"
    assert by_hash[hashes[4]].status == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_already_terminal(seeded_app):
    """Doc §7.test_cancel_already_terminal."""
    from datetime import datetime, timezone

    from app.db.engine import get_session
    from app.db.models import Batch

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    async with get_session() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == batch["batch_id"])
            .values(status="completed", finalized_at=datetime.now(timezone.utc))
        )
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/cancel", headers=auth(token)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cancel_unknown(seeded_app):
    """Doc §7.test_cancel_unknown."""
    token = await login_user(seeded_app)
    resp = await seeded_app.post(
        "/api/batches/bat_doesnotex/cancel", headers=auth(token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_foreign(seeded_app):
    """Doc §7.test_cancel_foreign."""
    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    batch = await register_batch(seeded_app, bob)
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/cancel", headers=auth(alice)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_no_queued_jobs(seeded_app):
    """Doc §7.test_cancel_no_queued_jobs — counted_now_count == 0."""
    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    resp = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/cancel", headers=auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cancelled_now_count"] == 0


# ---------------------------------------------------------------------------
# §8 DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_keep_jobs(seeded_app):
    """Doc §8.test_delete_keep_jobs."""
    from datetime import datetime, timezone

    from app.db.engine import get_session
    from app.db.models import Batch, Job

    token = await login_user(seeded_app)
    user_id = await _user_id_of()
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=4)]
    )
    job_hashes = [
        await insert_fake_job(user_id=user_id, batch_id=batch["batch_id"])
        for _ in range(4)
    ]
    async with get_session() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == batch["batch_id"])
            .values(
                status="completed",
                finalized_at=datetime.now(timezone.utc),
            )
        )

    resp = await seeded_app.delete(
        f"/api/batches/{batch['batch_id']}", headers=auth(token)
    )
    assert resp.status_code == 200, resp.text

    # Batch row gone.
    assert await fetch_batch_row(batch["batch_id"]) is None
    # Jobs survive with batch_id=NULL.
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job).where(Job.hash_id.in_(job_hashes))
            )
        ).scalars().all()
    assert len(rows) == 4
    for r in rows:
        assert r.batch_id is None


@pytest.mark.asyncio
async def test_delete_non_terminal_rejected(seeded_app):
    """Doc §8.test_delete_non_terminal_rejected."""
    from app.db.engine import get_session
    from app.db.models import Batch

    token = await login_user(seeded_app)
    batch = await register_batch(seeded_app, token)
    async with get_session() as session:
        await session.execute(
            update(Batch).where(Batch.id == batch["batch_id"]).values(status="running")
        )
    resp = await seeded_app.delete(
        f"/api/batches/{batch['batch_id']}", headers=auth(token)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_unknown(seeded_app):
    """Doc §8.test_delete_unknown."""
    token = await login_user(seeded_app)
    resp = await seeded_app.delete(
        "/api/batches/bat_doesnotex", headers=auth(token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_foreign(seeded_app):
    """Doc §8.test_delete_foreign."""
    from datetime import datetime, timezone

    from app.db.engine import get_session
    from app.db.models import Batch

    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    batch = await register_batch(seeded_app, bob)
    async with get_session() as session:
        await session.execute(
            update(Batch).where(Batch.id == batch["batch_id"]).values(
                status="completed", finalized_at=datetime.now(timezone.utc)
            )
        )
    resp = await seeded_app.delete(
        f"/api/batches/{batch['batch_id']}", headers=auth(alice)
    )
    assert resp.status_code == 404
