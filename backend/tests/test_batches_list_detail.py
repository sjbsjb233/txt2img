"""§4 + §5 of the testing doc — GET /api/batches and GET /api/batches/<id>."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from tests._batch_helpers import (
    auth,
    insert_fake_job,
    login_user,
    register_batch,
    slot_dict,
)


# ---------------------------------------------------------------------------
# Helpers — drive a batch into a target status via direct UPDATE.
# ---------------------------------------------------------------------------


async def _force_batch_status(batch_id: str, status: str) -> None:
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
# §4 LIST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_default_filter(seeded_app):
    """Doc §4.test_list_default_filter — default = non_terminal,recent."""
    token = await login_user(seeded_app)
    statuses = ["submitting", "running", "completed", "abandoned", "cancelled"]
    ids = []
    for s in statuses:
        b = await register_batch(seeded_app, token, title=s)
        ids.append((b["batch_id"], s))
        if s != "submitting":
            await _force_batch_status(b["batch_id"], s)

    resp = await seeded_app.get("/api/batches", headers=auth(token))
    assert resp.status_code == 200, resp.text
    visible = {it["batch_id"] for it in resp.json()["items"]}
    # All five should be visible: submitting/running are non-terminal,
    # the three terminals were finalized just now (within 24h → recent).
    for batch_id, _ in ids:
        assert batch_id in visible


@pytest.mark.asyncio
async def test_list_status_specific(seeded_app):
    """Doc §4.test_list_status_specific — explicit status filter."""
    token = await login_user(seeded_app)
    a = await register_batch(seeded_app, token, title="a")
    b = await register_batch(seeded_app, token, title="b")
    await _force_batch_status(b["batch_id"], "running")

    resp = await seeded_app.get(
        "/api/batches?status=running", headers=auth(token)
    )
    visible_ids = {it["batch_id"] for it in resp.json()["items"]}
    assert b["batch_id"] in visible_ids
    assert a["batch_id"] not in visible_ids


@pytest.mark.asyncio
async def test_list_status_all(seeded_app):
    """Doc §4.test_list_status_all."""
    token = await login_user(seeded_app)
    a = await register_batch(seeded_app, token, title="a")
    b = await register_batch(seeded_app, token, title="b")
    await _force_batch_status(b["batch_id"], "completed")
    # Force the completed batch's finalized_at very old so recent filter would skip it.
    from app.db.engine import get_session
    from app.db.models import Batch

    async with get_session() as session:
        old = datetime.now(timezone.utc) - timedelta(days=30)
        await session.execute(
            update(Batch)
            .where(Batch.id == b["batch_id"])
            .values(finalized_at=old, updated_at=old)
        )

    resp_default = await seeded_app.get("/api/batches", headers=auth(token))
    resp_all = await seeded_app.get(
        "/api/batches?status=all", headers=auth(token)
    )
    default_ids = {it["batch_id"] for it in resp_default.json()["items"]}
    all_ids = {it["batch_id"] for it in resp_all.json()["items"]}
    assert b["batch_id"] not in default_ids  # too old to be recent
    assert b["batch_id"] in all_ids
    assert a["batch_id"] in all_ids


@pytest.mark.asyncio
async def test_list_pagination(seeded_app):
    """Doc §4.test_list_pagination — cursor pagination."""
    token = await login_user(seeded_app)
    # Register 6 batches — the per-user concurrent cap is 3, so we
    # bounce some into terminal first.
    for _ in range(2):
        b = await register_batch(seeded_app, token)
        await _force_batch_status(b["batch_id"], "completed")
        b2 = await register_batch(seeded_app, token)
        await _force_batch_status(b2["batch_id"], "completed")
    # Add 2 more non-terminal to keep things mixed.
    await register_batch(seeded_app, token)
    await register_batch(seeded_app, token)

    page1 = await seeded_app.get(
        "/api/batches?status=all&limit=3", headers=auth(token)
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 3
    assert body1["next_cursor"] is not None

    page2 = await seeded_app.get(
        f"/api/batches?status=all&limit=3&cursor={body1['next_cursor']}",
        headers=auth(token),
    )
    body2 = page2.json()
    # No duplicates between pages.
    p1 = {it["batch_id"] for it in body1["items"]}
    p2 = {it["batch_id"] for it in body2["items"]}
    assert p1.isdisjoint(p2)


@pytest.mark.asyncio
async def test_list_sort_order(seeded_app):
    """Doc §4.test_list_sort_order — newest activity bubbles to the top."""
    token = await login_user(seeded_app)
    # Register two batches a few seconds apart by tweaking updated_at.
    from app.db.engine import get_session
    from app.db.models import Batch

    a = await register_batch(seeded_app, token, title="a")
    b = await register_batch(seeded_app, token, title="b")
    async with get_session() as session:
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.execute(
            update(Batch)
            .where(Batch.id == a["batch_id"])
            .values(updated_at=old)
        )

    resp = await seeded_app.get("/api/batches", headers=auth(token))
    items = resp.json()["items"]
    # b was just registered (more recent updated_at) → first.
    assert items[0]["batch_id"] == b["batch_id"]


@pytest.mark.asyncio
async def test_list_foreign_user_isolated(seeded_app):
    """Doc §4.test_list_foreign_user_isolated."""
    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    a_batch = await register_batch(seeded_app, alice, title="a")
    b_batch = await register_batch(seeded_app, bob, title="b")

    resp = await seeded_app.get("/api/batches", headers=auth(bob))
    visible = {it["batch_id"] for it in resp.json()["items"]}
    assert a_batch["batch_id"] not in visible
    assert b_batch["batch_id"] in visible


@pytest.mark.asyncio
async def test_list_anonymous_rejected(seeded_app):
    """Doc §4.test_list_anonymous_rejected."""
    resp = await seeded_app.get("/api/batches")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_recent_window(seeded_app):
    """Doc §4.test_list_recent_window — terminal in 24h is in default view."""
    from app.db.engine import get_session
    from app.db.models import Batch

    token = await login_user(seeded_app)
    # Recent terminal (23h ago).
    fresh = await register_batch(seeded_app, token, title="fresh")
    # Stale terminal (25h ago).
    stale = await register_batch(seeded_app, token, title="stale")

    async with get_session() as session:
        twenty_three = datetime.now(timezone.utc) - timedelta(hours=23)
        twenty_five = datetime.now(timezone.utc) - timedelta(hours=25)
        await session.execute(
            update(Batch).where(Batch.id == fresh["batch_id"]).values(
                status="completed",
                finalized_at=twenty_three,
                updated_at=twenty_three,
            )
        )
        await session.execute(
            update(Batch).where(Batch.id == stale["batch_id"]).values(
                status="completed",
                finalized_at=twenty_five,
                updated_at=twenty_five,
            )
        )

    resp = await seeded_app.get("/api/batches", headers=auth(token))
    ids = {it["batch_id"] for it in resp.json()["items"]}
    assert fresh["batch_id"] in ids
    assert stale["batch_id"] not in ids


# ---------------------------------------------------------------------------
# §5 DETAIL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_basic(seeded_app):
    """Doc §5.test_detail_basic — full schema returned."""
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app,
        token,
        slots=[
            slot_dict(stable_idx=1, image_count=2, set_id="set_aaaaaaaaaa"),
            slot_dict(stable_idx=2, image_count=2, set_id="set_bbbbbbbbbb"),
        ],
    )
    resp = await seeded_app.get(
        f"/api/batches/{batch['batch_id']}", headers=auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "spec" in body
    assert "in_flight_count" in body
    assert len(body["slots"]) == 2
    # slot order matches stable_idx ASC
    assert [s["stable_idx"] for s in body["slots"]] == [1, 2]


@pytest.mark.asyncio
async def test_detail_in_flight_derived(seeded_app):
    """Doc §5.test_detail_in_flight_derived — counters reflect actual rows."""
    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    token = await login_user(seeded_app)
    async with get_session() as session:
        user = (
            await session.execute(
                select(User).where(User.username == "alice")
            )
        ).scalar_one()
        user_id = user.id
    batch = await register_batch(
        seeded_app,
        token,
        slots=[slot_dict(stable_idx=1, image_count=6, set_id="set_xxxxxxxxxx")],
    )
    # Insert 6 jobs, drive 2→SUCCEEDED, 1→FAILED, leave 3 QUEUED.
    succeeded_hashes = []
    failed_hash = None
    for i in range(6):
        h = await insert_fake_job(
            user_id=user_id,
            batch_id=batch["batch_id"],
            set_id="set_xxxxxxxxxx",
        )
        if i < 2:
            succeeded_hashes.append(h)
        elif i == 2:
            failed_hash = h
    from tests._batch_helpers import flush_emitter, force_job_status

    for h in succeeded_hashes:
        await force_job_status(h, "SUCCEEDED")
    await force_job_status(failed_hash, "FAILED")
    await flush_emitter()

    resp = await seeded_app.get(
        f"/api/batches/{batch['batch_id']}", headers=auth(token)
    )
    body = resp.json()
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 1
    assert body["in_flight_count"] == 3  # 3 still QUEUED


@pytest.mark.asyncio
async def test_detail_unknown_returns_404(seeded_app):
    """Doc §5.test_detail_unknown_returns_404."""
    token = await login_user(seeded_app)
    resp = await seeded_app.get(
        "/api/batches/bat_doesnotex", headers=auth(token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_detail_foreign_returns_404(seeded_app):
    """Doc §5.test_detail_foreign_returns_404."""
    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    batch = await register_batch(seeded_app, bob)
    resp = await seeded_app.get(
        f"/api/batches/{batch['batch_id']}", headers=auth(alice)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_detail_includes_job_hash_ids(seeded_app):
    """Doc §5.test_detail_includes_job_hash_ids."""
    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    token = await login_user(seeded_app)
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.username == "alice"))
        ).scalar_one()
        user_id = user.id
    batch = await register_batch(
        seeded_app,
        token,
        slots=[slot_dict(stable_idx=1, image_count=3, set_id="set_aBcDeFgHij")],
    )
    hashes = []
    for _ in range(3):
        h = await insert_fake_job(
            user_id=user_id,
            batch_id=batch["batch_id"],
            set_id="set_aBcDeFgHij",
        )
        hashes.append(h)

    resp = await seeded_app.get(
        f"/api/batches/{batch['batch_id']}", headers=auth(token)
    )
    slot = resp.json()["slots"][0]
    assert set(slot["job_hash_ids"]) == set(hashes)
