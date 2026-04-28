from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.db.engine import get_session
from app.db.jobs_repository import JobsRepository
from app.db.models import Job, User
from app.domain.job_queue import JobQueue, QueuedJob
from app.domain.job_scheduler import JobScheduler
from app.utils.ids import new_user_id
from app.utils.security import hash_password


def _qjob(tier: str, idx: int) -> QueuedJob:
    return QueuedJob(
        hash_id=f"j_{idx:012d}",
        job_id=f"job_{idx:012d}",
        user_id="u_test",
        tier=tier,
        model="gpt-image-2",
        queued_at=datetime.now(timezone.utc),
    )


async def _bootstrap() -> None:
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    await get_config_center().load_from_db()
    await get_tier_config().load_from_db()


async def _create_user(tier: str = "free") -> User:
    user = User(
        id=new_user_id(),
        username=f"user_{tier}",
        password_hash=hash_password("pw123456"),
        role="user",
        tier=tier,
    )
    async with get_session() as session:
        session.add(user)
    return user


@pytest.mark.asyncio
async def test_wfq_full_load_matches_8_4_2_1_distribution() -> None:
    queue = JobQueue()
    idx = 0
    for tier in ("vip", "premium", "standard", "free"):
        for _ in range(15):
            idx += 1
            await queue.enqueue(_qjob(tier, idx))

    popped = [await queue.pop_next() for _ in range(15)]
    counts: dict[str, int] = {}
    for job in popped:
        assert job is not None
        counts[job.lane_tier or job.tier] = counts.get(job.lane_tier or job.tier, 0) + 1

    assert counts == {"vip": 8, "premium": 4, "standard": 2, "free": 1}


@pytest.mark.asyncio
async def test_deadline_promotion_moves_job_up_one_lane_and_flags_db(
    initialized_db: None,
) -> None:
    await _bootstrap()
    from app.domain.config_center import get_config_center

    await get_config_center().set_many({"scheduler.deadline_promotion_seconds": 1})
    user = await _create_user("free")
    old = datetime.now(timezone.utc) - timedelta(seconds=10)

    repo = JobsRepository()
    async with get_session() as session:
        created = await repo.insert_queued(
            user_id=user.id,
            tier_at_submit="free",
            model="gpt-image-2",
            params_json=json.dumps({"model": "gpt-image-2", "prompt": "x"}),
            flags_json="{}",
            session=session,
        )
        await session.execute(
            update(Job)
            .where(Job.hash_id == created.hash_id)
            .values(queued_at=old, created_at=old)
        )

    async with get_session() as session:
        job = (
            await session.execute(select(Job).where(Job.hash_id == created.hash_id))
        ).scalar_one()

    queue = JobQueue()
    await queue.enqueue(QueuedJob.from_job(job))
    popped = await queue.pop_next()

    assert popped is not None
    assert popped.tier == "free"
    assert popped.lane_tier == "standard"
    assert popped.promoted is True

    async with get_session() as session:
        flags = (
            await session.execute(
                select(Job.flags_json).where(Job.hash_id == created.hash_id)
            )
        ).scalar_one()
    assert json.loads(flags)["promoted"] is True


@pytest.mark.asyncio
async def test_scheduler_respects_per_user_concurrency_limit(
    initialized_db: None,
) -> None:
    await _bootstrap()
    queue = JobQueue()
    scheduler = JobScheduler(queue)
    release = asyncio.Event()
    started: list[QueuedJob] = []

    class BlockingExecutor:
        async def execute(self, job: QueuedJob) -> None:
            started.append(job)
            if job.user_id == "u_busy":
                await release.wait()

    await queue.enqueue(
        QueuedJob(
            hash_id="j_000000000001",
            job_id="job_000000000001",
            user_id="u_busy",
            tier="free",
            model="gpt-image-2",
            queued_at=datetime.now(timezone.utc),
        )
    )
    await queue.enqueue(
        QueuedJob(
            hash_id="j_000000000002",
            job_id="job_000000000002",
            user_id="u_busy",
            tier="free",
            model="gpt-image-2",
            queued_at=datetime.now(timezone.utc),
        )
    )
    await queue.enqueue(
        QueuedJob(
            hash_id="j_000000000003",
            job_id="job_000000000003",
            user_id="u_other",
            tier="free",
            model="gpt-image-2",
            queued_at=datetime.now(timezone.utc),
        )
    )

    task = asyncio.create_task(scheduler.run_forever(BlockingExecutor()))
    try:
        for _ in range(20):
            if len(started) >= 2:
                break
            await asyncio.sleep(0.05)
        assert [j.user_id for j in started] == ["u_busy", "u_other"]
    finally:
        release.set()
        await scheduler.drain(timeout=2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
