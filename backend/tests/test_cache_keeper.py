"""Unit tests for ``app.domain.cache_keeper.refresh_disk_usage``.

These tests don't bring up the full FastAPI app — they exercise the
function directly against a freshly-migrated SQLite database, and lay
out tiny on-disk job directories so the byte sums are predictable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select


def _data_root_jobs() -> Path:
    return Path(os.environ["DATA_ROOT"]).resolve() / "jobs"


def _make_job_dir_on_disk(hash_id: str, payload_bytes: int) -> Path:
    """Create ``data/jobs/<hash_id>/outputs/01_original.png`` of the given size."""
    p = _data_root_jobs() / hash_id / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    (p / "01_original.png").write_bytes(b"x" * payload_bytes)
    return p.parent


async def _insert_job_row(
    *,
    hash_id: str,
    user_id: str = "u_test",
    status: str,
    created_at: datetime,
    finished_at: datetime | None = None,
    seq_no: int | None = None,
) -> None:
    """Minimal ``jobs`` row insertion helper. Avoids the full lifecycle layer."""
    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import func, select as _select

    async with get_session() as session:
        if seq_no is None:
            cur_max = (
                await session.execute(
                    _select(func.coalesce(func.max(Job.seq_no), 0)).where(
                        Job.user_id == user_id
                    )
                )
            ).scalar_one()
            seq_no = int(cur_max) + 1
        session.add(
            Job(
                id="job_" + hash_id[2:],  # internal id
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit="free",
                seq_no=seq_no,
                model="gemini-3.1-flash-image-preview",
                params_json="{}",
                flags_json="{}",
                status=status,
                created_at=created_at,
                finished_at=finished_at,
            )
        )


async def _insert_test_user() -> None:
    """The jobs FK needs a matching users row; we don't go through auth here."""
    from app.db.engine import get_session
    from app.db.models import User

    async with get_session() as session:
        # Avoid IntegrityError if a previous test in the same session reused
        # the user — tests in this file do their own cleanup via fresh DB.
        session.add(
            User(
                id="u_test",
                username="tester",
                password_hash="x",
                role="user",
                tier="free",
                today_reset_date="2026-04-28",
            )
        )


# ---------------------------------------------------------------------------
# Empty case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_disk_usage_empty_writes_all_scopes(
    initialized_db: None,
) -> None:
    """No on-disk jobs at all — every scope must still get a zero row."""
    from app.db.engine import get_session
    from app.db.models import DiskUsage
    from app.domain.cache_keeper import ALL_SCOPES, refresh_disk_usage

    totals = await refresh_disk_usage()
    assert set(totals.keys()) == set(ALL_SCOPES)
    for scope in ALL_SCOPES:
        assert totals[scope].bytes == 0
        assert totals[scope].job_count == 0

    async with get_session() as session:
        rows = (await session.execute(select(DiskUsage))).scalars().all()
    by_scope = {r.scope: r for r in rows}
    assert set(by_scope) == set(ALL_SCOPES)


# ---------------------------------------------------------------------------
# Mixed jobs: age + status categorisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_disk_usage_categorises_by_age_and_status(
    initialized_db: None,
) -> None:
    from app.domain.cache_keeper import (
        SCOPE_JOBS_CANCELLED,
        SCOPE_JOBS_FAILED_OLDER_THAN_1D,
        SCOPE_JOBS_OLDER_THAN_7D,
        SCOPE_JOBS_OLDER_THAN_30D,
        SCOPE_JOBS_OLDER_THAN_90D,
        SCOPE_JOBS_TOTAL,
        refresh_disk_usage,
    )

    await _insert_test_user()

    now = datetime.now(timezone.utc)

    # 1) Fresh successful job, 100 bytes, today.
    _make_job_dir_on_disk("j_aaaaaaaaaaa1", 100)
    await _insert_job_row(
        hash_id="j_aaaaaaaaaaa1",
        status="SUCCEEDED",
        created_at=now,
        finished_at=now,
    )

    # 2) Old (10 days) succeeded job, 200 bytes.
    _make_job_dir_on_disk("j_bbbbbbbbbbb2", 200)
    await _insert_job_row(
        hash_id="j_bbbbbbbbbbb2",
        status="SUCCEEDED",
        created_at=now - timedelta(days=10),
        finished_at=now - timedelta(days=10),
    )

    # 3) 95-day-old succeeded job, 300 bytes — counts in all three age scopes.
    _make_job_dir_on_disk("j_ccccccccccc3", 300)
    await _insert_job_row(
        hash_id="j_ccccccccccc3",
        status="SUCCEEDED",
        created_at=now - timedelta(days=95),
        finished_at=now - timedelta(days=95),
    )

    # 4) FAILED job that finished 2 days ago, 400 bytes.
    _make_job_dir_on_disk("j_ddddddddddd4", 400)
    await _insert_job_row(
        hash_id="j_ddddddddddd4",
        status="FAILED",
        created_at=now - timedelta(days=2),
        finished_at=now - timedelta(days=2),
    )

    # 5) CANCELLED today, 500 bytes.
    _make_job_dir_on_disk("j_eeeeeeeeeee5", 500)
    await _insert_job_row(
        hash_id="j_eeeeeeeeeee5",
        status="CANCELLED",
        created_at=now,
    )

    totals = await refresh_disk_usage()

    # jobs_total covers all 5 dirs.
    assert totals[SCOPE_JOBS_TOTAL].job_count == 5
    assert totals[SCOPE_JOBS_TOTAL].bytes == 100 + 200 + 300 + 400 + 500

    # 7d: jobs (2) 10d, (3) 95d, (4) failed 2d ago = 200+300+400, 3 jobs.
    assert totals[SCOPE_JOBS_OLDER_THAN_7D].job_count == 2  # only 10d + 95d
    assert totals[SCOPE_JOBS_OLDER_THAN_7D].bytes == 200 + 300

    # 30d: only 95d job qualifies.
    assert totals[SCOPE_JOBS_OLDER_THAN_30D].job_count == 1
    assert totals[SCOPE_JOBS_OLDER_THAN_30D].bytes == 300

    # 90d: only 95d job.
    assert totals[SCOPE_JOBS_OLDER_THAN_90D].job_count == 1
    assert totals[SCOPE_JOBS_OLDER_THAN_90D].bytes == 300

    # Failed > 1d: only job (4).
    assert totals[SCOPE_JOBS_FAILED_OLDER_THAN_1D].job_count == 1
    assert totals[SCOPE_JOBS_FAILED_OLDER_THAN_1D].bytes == 400

    # Cancelled (any age): only job (5).
    assert totals[SCOPE_JOBS_CANCELLED].job_count == 1
    assert totals[SCOPE_JOBS_CANCELLED].bytes == 500


# ---------------------------------------------------------------------------
# Orphan dir on disk (no DB row)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_disk_usage_orphan_dir_counts_in_total_only(
    initialized_db: None,
) -> None:
    """A dir on disk with no jobs row contributes only to ``jobs_total``."""
    from app.domain.cache_keeper import (
        ALL_SCOPES,
        SCOPE_JOBS_TOTAL,
        refresh_disk_usage,
    )

    _make_job_dir_on_disk("j_orphanaaaaaa", 123)

    totals = await refresh_disk_usage()
    assert totals[SCOPE_JOBS_TOTAL].job_count == 1
    assert totals[SCOPE_JOBS_TOTAL].bytes == 123
    for scope in ALL_SCOPES:
        if scope == SCOPE_JOBS_TOTAL:
            continue
        assert totals[scope].job_count == 0
        assert totals[scope].bytes == 0


# ---------------------------------------------------------------------------
# Stray non-job dirs are ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_disk_usage_skips_invalid_dirnames(
    initialized_db: None,
) -> None:
    """Anything in jobs/ that isn't ``j_<12>`` must not be counted."""
    from app.domain.cache_keeper import SCOPE_JOBS_TOTAL, refresh_disk_usage

    bad = _data_root_jobs() / "tmp_workspace"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "f.bin").write_bytes(b"x" * 999)

    short = _data_root_jobs() / "j_short"
    short.mkdir(parents=True, exist_ok=True)
    (short / "f.bin").write_bytes(b"x" * 999)

    totals = await refresh_disk_usage()
    assert totals[SCOPE_JOBS_TOTAL].job_count == 0
    assert totals[SCOPE_JOBS_TOTAL].bytes == 0


# ---------------------------------------------------------------------------
# Idempotency: running twice produces the same numbers, doesn't double-write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_disk_usage_is_idempotent_upsert(
    initialized_db: None,
) -> None:
    from app.db.engine import get_session
    from app.db.models import DiskUsage
    from app.domain.cache_keeper import ALL_SCOPES, refresh_disk_usage

    await _insert_test_user()
    _make_job_dir_on_disk("j_idempotentxx", 64)
    await _insert_job_row(
        hash_id="j_idempotentxx",
        status="SUCCEEDED",
        created_at=datetime.now(timezone.utc),
    )

    await refresh_disk_usage()
    await refresh_disk_usage()

    async with get_session() as session:
        rows = (await session.execute(select(DiskUsage))).scalars().all()

    # One row per scope, no duplicates.
    scopes = [r.scope for r in rows]
    assert sorted(scopes) == sorted(ALL_SCOPES)
    assert len(scopes) == len(set(scopes))
