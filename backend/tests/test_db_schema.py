"""Schema-shape tests.

These run against a real (file-backed) SQLite database so we exercise the
exact pragmas, CHECK constraints, and indexes that production hits. The
goal isn't exhaustive table-by-table coverage — it's to lock down the
invariants the rest of the codebase depends on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _path_from_db_url() -> str:
    import os

    url = os.environ["DB_URL"]
    prefix = "sqlite+aiosqlite:///"
    assert url.startswith(prefix)
    return url[len(prefix) :]


@pytest.mark.asyncio
async def test_all_tables_exist(initialized_db: None) -> None:
    db_path = _path_from_db_url()
    raw = sqlite3.connect(db_path)
    rows = raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    raw.close()
    actual = {r[0] for r in rows}

    expected = {
        "alembic_version",
        "announcement_reads",
        "announcements",
        "audit_log",
        "billing_ledger",
        "config",
        "disk_usage",
        "images",
        "job_references",
        "jobs",
        "login_attempts",
        "provider_model_tier_access",
        "provider_models",
        "provider_tier_access",
        "providers",
        "session_jobs",
        "sessions",
        "tiers",
        "users",
    }
    assert actual == expected


@pytest.mark.asyncio
async def test_indexes_present(initialized_db: None) -> None:
    db_path = _path_from_db_url()
    raw = sqlite3.connect(db_path)
    rows = raw.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    raw.close()
    actual = {r[0] for r in rows}

    # Spot-check the indexes called out in the design doc §14.
    must_have = {
        "idx_audit_actor_ts",
        "idx_images_job",
        "idx_jobs_set",
        "idx_jobs_status_created",
        "idx_jobs_user_status",
        "idx_jobs_user_updated",
        "idx_ledger_provider_time",
        "idx_login_attempts_user_time",
        "idx_sessions_user",
        "idx_users_status_tier",
    }
    missing = must_have - actual
    assert not missing, f"missing indexes: {missing}"


@pytest.mark.asyncio
async def test_wal_mode_enabled(initialized_db: None) -> None:
    """WAL is the only journal mode under which our concurrency assumptions hold."""
    from app.db.engine import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(_text("PRAGMA journal_mode"))
        mode = result.scalar_one()
    assert str(mode).lower() == "wal"


@pytest.mark.asyncio
async def test_foreign_keys_enforced(initialized_db: None) -> None:
    """Without ``PRAGMA foreign_keys=ON`` the FKs in models.py are advisory only."""
    from app.db.engine import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(_text("PRAGMA foreign_keys"))
        on = result.scalar_one()
    assert int(on) == 1


@pytest.mark.asyncio
async def test_check_constraint_user_role(initialized_db: None) -> None:
    """Users.role CHECK constraint must reject anything other than admin/user."""
    from sqlalchemy.exc import IntegrityError

    from app.db.engine import get_session
    from app.db.models import User

    with pytest.raises(IntegrityError):
        async with get_session() as session:
            session.add(
                User(
                    id="u_bad_role_test",
                    username="badrole",
                    password_hash="x",
                    role="superadmin",  # not in CHECK constraint
                    tier="vip",
                )
            )


@pytest.mark.asyncio
async def test_check_constraint_job_status(initialized_db: None) -> None:
    """Jobs.status CHECK rejects unknown states."""
    import json

    from sqlalchemy.exc import IntegrityError

    from app.db.engine import get_session
    from app.db.models import Job, User

    async with get_session() as session:
        session.add(
            User(
                id="u_for_job_status_test",
                username="jobstatususer",
                password_hash="x",
                role="user",
                tier="free",
            )
        )

    with pytest.raises(IntegrityError):
        async with get_session() as session:
            session.add(
                Job(
                    id="job_bad_status",
                    hash_id="j_badstatus000",
                    user_id="u_for_job_status_test",
                    tier_at_submit="free",
                    seq_no=1,
                    model="gemini-3.1-flash-image-preview",
                    params_json=json.dumps({}),
                    status="WEIRD",  # not allowed
                )
            )


@pytest.mark.asyncio
async def test_unique_username(initialized_db: None) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.db.engine import get_session
    from app.db.models import User

    async with get_session() as session:
        session.add(
            User(
                id="u_uniq_a",
                username="dupuser",
                password_hash="x",
                role="user",
                tier="free",
            )
        )
    with pytest.raises(IntegrityError):
        async with get_session() as session:
            session.add(
                User(
                    id="u_uniq_b",
                    username="dupuser",
                    password_hash="y",
                    role="user",
                    tier="free",
                )
            )


@pytest.mark.asyncio
async def test_unique_user_seq_no(initialized_db: None) -> None:
    """``(user_id, seq_no)`` must be unique — protects sequence allocator."""
    import json

    from sqlalchemy.exc import IntegrityError

    from app.db.engine import get_session
    from app.db.models import Job, User

    # Create user + first job in independent units of work; the FK won't
    # resolve until the user row is durably committed.
    async with get_session() as session:
        session.add(
            User(
                id="u_seq_test",
                username="sequser",
                password_hash="x",
                role="user",
                tier="free",
            )
        )
    async with get_session() as session:
        session.add(
            Job(
                id="job_seq_a",
                hash_id="j_seqtestaaaa",
                user_id="u_seq_test",
                tier_at_submit="free",
                seq_no=1,
                model="m",
                params_json=json.dumps({}),
                status="QUEUED",
            )
        )

    with pytest.raises(IntegrityError):
        async with get_session() as session:
            session.add(
                Job(
                    id="job_seq_b",
                    hash_id="j_seqtestbbbb",
                    user_id="u_seq_test",
                    tier_at_submit="free",
                    seq_no=1,  # duplicate
                    model="m",
                    params_json=json.dumps({}),
                    status="QUEUED",
                )
            )


def _text(sql: str):
    """Lazy import of sqlalchemy.text to keep the top of file slim."""
    from sqlalchemy import text

    return text(sql)
