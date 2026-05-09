"""§17 of the testing doc — DB migration / compat assertions for batches."""

from __future__ import annotations

import os
import sqlite3

import pytest


def _db_path() -> str:
    url = os.environ["DB_URL"]
    prefix = "sqlite+aiosqlite:///"
    assert url.startswith(prefix)
    return url[len(prefix):]


@pytest.mark.asyncio
async def test_batches_table_columns(initialized_db):
    """Doc §17.test_batches_table_columns."""
    raw = sqlite3.connect(_db_path())
    cols = {
        r[1]
        for r in raw.execute("PRAGMA table_info(batches)").fetchall()
    }
    raw.close()
    expected = {
        "id",
        "user_id",
        "title",
        "status",
        "spec_json",
        "total_job_count",
        "submitted_count",
        "succeeded_count",
        "failed_count",
        "cancelled_count",
        "last_activity_at",
        "created_at",
        "updated_at",
        "finalized_at",
    }
    assert expected.issubset(cols), f"missing {expected - cols}"


@pytest.mark.asyncio
async def test_batches_indexes_present(initialized_db):
    """Three indexes on batches per migration 0005."""
    raw = sqlite3.connect(_db_path())
    rows = raw.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='batches' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    raw.close()
    names = {r[0] for r in rows}
    assert "idx_batches_user_status" in names
    assert "idx_batches_user_updated" in names
    assert "idx_batches_watchdog" in names


@pytest.mark.asyncio
async def test_jobs_batch_id_column(initialized_db):
    """Doc §17.test_jobs_batch_id_column."""
    raw = sqlite3.connect(_db_path())
    cols = [r[1] for r in raw.execute("PRAGMA table_info(jobs)").fetchall()]
    indexes = [
        r[0]
        for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"
        ).fetchall()
    ]
    raw.close()
    assert "batch_id" in cols
    assert "idx_jobs_batch" in indexes


@pytest.mark.asyncio
async def test_old_jobs_have_null_batch_id(seeded_app):
    """Doc §17.test_old_jobs_have_null_batch_id — old paths still work.

    A job inserted via the v0.2-shaped helper (no batch_id) should have
    batch_id=NULL and continue to be returned by archive endpoints.
    """
    from app.db.engine import get_session
    from app.db.jobs_repository import get_jobs_repository, serialise_params
    from app.db.models import User
    from sqlalchemy import select
    from app.utils.security import hash_password

    async with get_session() as session:
        user = User(
            id="u_olduser",
            username="olduser",
            password_hash=hash_password("p"),
            role="user",
            tier="premium",
        )
        session.add(user)

    repo = get_jobs_repository()
    async with get_session() as session:
        created = await repo.insert_queued(
            user_id="u_olduser",
            tier_at_submit="premium",
            model="gpt-image-2",
            params_json=serialise_params({"model": "gpt-image-2", "prompt": "x"}),
            session=session,
        )

    from app.db.models import Job

    async with get_session() as session:
        row = (
            await session.execute(select(Job).where(Job.hash_id == created.hash_id))
        ).scalar_one()
    assert row.batch_id is None
