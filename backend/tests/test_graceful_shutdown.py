"""Tests for the graceful-shutdown reconciliation helper.

The helper has two callers (lifespan startup, lifespan shutdown), but
the contract is identical: every ``RUNNING`` job becomes ``FAILED`` with
``SERVER_RESTART`` and the user's daily quota is refunded.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import Job, User
from app.domain.graceful_shutdown import (
    SERVER_RESTART_REASON,
    force_fail_running_jobs,
)
from app.domain.quota_guard import get_quota_guard
from app.utils.ids import new_job_hash_id, new_job_internal_id, new_user_id


async def _seed_user(today_count: int = 0, tier: str = "premium") -> str:
    user_id = new_user_id()
    async with get_session() as session:
        session.add(
            User(
                id=user_id,
                username=f"alice-{user_id[-4:]}",
                password_hash="argon2id$placeholder",
                role="user",
                tier=tier,
                today_count=today_count,
                today_reset_date=datetime.now(timezone.utc).date().isoformat(),
            )
        )
    return user_id


async def _seed_running_job(
    user_id: str, *, model: str = "gpt-image-2", tier: str = "premium"
) -> str:
    hash_id = new_job_hash_id()
    job_id = new_job_internal_id()
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        # Atomic seq_no allocation: read+update the user counter inside
        # the same transaction so concurrent calls in this helper produce
        # unique values for the (user_id, seq_no) UNIQUE index.
        from sqlalchemy import update as _update

        row = (
            await session.execute(
                _update(User)
                .where(User.id == user_id)
                .values(last_seq_no=User.last_seq_no + 1)
                .returning(User.last_seq_no)
            )
        ).one_or_none()
        seq_no = int(row[0]) if row is not None else 1
        session.add(
            Job(
                id=job_id,
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit=tier,
                seq_no=seq_no,
                model=model,
                params_json=json.dumps({"prompt": "x"}),
                flags_json="{}",
                status="RUNNING",
                created_at=now,
                queued_at=now,
                dispatched_at=now,
                started_at=now,
                updated_at=now,
            )
        )
    return hash_id


@pytest.mark.asyncio
async def test_force_fail_running_jobs_zero_rows(initialized_db: None) -> None:
    """Empty sweep returns 0 and does not error."""
    forced = await force_fail_running_jobs()
    assert forced == 0


@pytest.mark.asyncio
async def test_force_fail_running_jobs_marks_failed_with_reason(
    initialized_db: None,
) -> None:
    """Each RUNNING job ends up FAILED with SERVER_RESTART reason."""
    user_id = await _seed_user(today_count=3)
    hash_a = await _seed_running_job(user_id)
    hash_b = await _seed_running_job(user_id)

    forced = await force_fail_running_jobs()
    assert forced == 2

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job.hash_id, Job.status, Job.status_reason).where(
                    Job.hash_id.in_([hash_a, hash_b])
                )
            )
        ).all()
    statuses = {h: (s, r) for h, s, r in rows}
    assert statuses[hash_a] == ("FAILED", SERVER_RESTART_REASON)
    assert statuses[hash_b] == ("FAILED", SERVER_RESTART_REASON)


@pytest.mark.asyncio
async def test_force_fail_refunds_quota(initialized_db: None) -> None:
    """Quota counter shrinks by exactly the number of force-failed jobs."""
    user_id = await _seed_user(today_count=5)
    await _seed_running_job(user_id)
    await _seed_running_job(user_id)
    await _seed_running_job(user_id)

    forced = await force_fail_running_jobs()
    assert forced == 3

    quota = get_quota_guard()
    remaining = await quota.today_count(user_id)
    assert remaining == 2  # 5 - 3 refunded


@pytest.mark.asyncio
async def test_force_fail_refund_clamped_at_zero(initialized_db: None) -> None:
    """A user whose counter started below the refund total never goes negative."""
    user_id = await _seed_user(today_count=1)
    await _seed_running_job(user_id)
    await _seed_running_job(user_id)
    await _seed_running_job(user_id)

    forced = await force_fail_running_jobs()
    assert forced == 3

    quota = get_quota_guard()
    remaining = await quota.today_count(user_id)
    assert remaining == 0


@pytest.mark.asyncio
async def test_force_fail_skips_non_running(initialized_db: None) -> None:
    """SUCCEEDED / FAILED / CANCELLED rows are not touched."""
    user_id = await _seed_user(today_count=4)
    running_hash = await _seed_running_job(user_id)

    succeeded_hash = new_job_hash_id()
    cancelled_hash = new_job_hash_id()
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        from sqlalchemy import update as _update

        row = (
            await session.execute(
                _update(User)
                .where(User.id == user_id)
                .values(last_seq_no=User.last_seq_no + 1)
                .returning(User.last_seq_no)
            )
        ).one_or_none()
        succ_seq = int(row[0]) if row is not None else 2
        row = (
            await session.execute(
                _update(User)
                .where(User.id == user_id)
                .values(last_seq_no=User.last_seq_no + 1)
                .returning(User.last_seq_no)
            )
        ).one_or_none()
        canc_seq = int(row[0]) if row is not None else 3
        session.add_all(
            [
                Job(
                    id=new_job_internal_id(),
                    hash_id=succeeded_hash,
                    user_id=user_id,
                    tier_at_submit="premium",
                    seq_no=succ_seq,
                    model="gpt-image-2",
                    params_json="{}",
                    flags_json="{}",
                    status="SUCCEEDED",
                    created_at=now,
                    queued_at=now,
                    finished_at=now,
                    updated_at=now,
                ),
                Job(
                    id=new_job_internal_id(),
                    hash_id=cancelled_hash,
                    user_id=user_id,
                    tier_at_submit="premium",
                    seq_no=canc_seq,
                    model="gpt-image-2",
                    params_json="{}",
                    flags_json="{}",
                    status="CANCELLED",
                    created_at=now,
                    queued_at=now,
                    finished_at=now,
                    updated_at=now,
                ),
            ]
        )

    forced = await force_fail_running_jobs()
    assert forced == 1  # only the RUNNING row

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job.hash_id, Job.status).where(
                    Job.hash_id.in_([running_hash, succeeded_hash, cancelled_hash])
                )
            )
        ).all()
    statuses = {h: s for h, s in rows}
    assert statuses[running_hash] == "FAILED"
    assert statuses[succeeded_hash] == "SUCCEEDED"
    assert statuses[cancelled_hash] == "CANCELLED"


@pytest.mark.asyncio
async def test_force_fail_idempotent(initialized_db: None) -> None:
    """Calling the sweep twice in a row is safe — the second pass is a no-op."""
    user_id = await _seed_user(today_count=2)
    await _seed_running_job(user_id)

    first = await force_fail_running_jobs()
    second = await force_fail_running_jobs()
    assert first == 1
    assert second == 0

    quota = get_quota_guard()
    remaining = await quota.today_count(user_id)
    assert remaining == 1
