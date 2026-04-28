"""WFQ scheduler loop and worker-pool accounting."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Iterable, Protocol

from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import Job, Provider
from app.domain.job_executor import get_job_executor
from app.domain.job_queue import JobQueue, QueuedJob, get_job_queue
from app.domain.runtime_configs import SchedulerConfig
from app.domain.tier_config import get_tier_config

logger = logging.getLogger("txt2img.scheduler")


class ExecutorLike(Protocol):
    async def execute(self, job: QueuedJob) -> None: ...


class JobScheduler:
    """Continuously pop WFQ jobs and run them in bounded worker tasks."""

    def __init__(
        self,
        queue: JobQueue | None = None,
        *,
        config: SchedulerConfig | None = None,
    ) -> None:
        self._queue = queue or get_job_queue()
        self._config = config or SchedulerConfig()
        self._tasks: set[asyncio.Task[None]] = set()
        self._task_users: dict[asyncio.Task[None], str] = {}
        self._running_by_user: dict[str, int] = {}
        self._stop = asyncio.Event()
        self._drained = asyncio.Event()
        self._drained.set()

    @property
    def running_count(self) -> int:
        return len(self._tasks)

    async def run_forever(self, executor: ExecutorLike | None = None) -> None:
        """Main scheduling loop. Runs until ``drain`` or cancellation."""
        worker = executor or get_job_executor()
        try:
            while not self._stop.is_set():
                self._prune_done()
                if self._has_free_worker_slot():
                    job = await self._queue.pop_next()
                    if job is not None:
                        if not self._user_has_slot(job):
                            await self._queue.requeue(job)
                            await asyncio.sleep(0.05)
                            continue
                        self._dispatch(job, worker)
                        continue
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        finally:
            await self._wait_for_tasks()

    async def drain(self, *, timeout: float = 30.0) -> None:
        """Stop dispatching new jobs and wait for in-flight workers."""
        self._stop.set()
        self._queue.close()
        if not self._tasks:
            self._drained.set()
            return
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._drained.wait(), timeout=timeout)

    def reset_for_tests(self) -> None:
        self._stop = asyncio.Event()
        self._drained = asyncio.Event()
        self._drained.set()
        self._tasks.clear()
        self._task_users.clear()
        self._running_by_user.clear()

    def _dispatch(self, job: QueuedJob, executor: ExecutorLike) -> None:
        self._drained.clear()
        self._running_by_user[job.user_id] = (
            self._running_by_user.get(job.user_id, 0) + 1
        )
        task = asyncio.create_task(
            executor.execute(job),
            name=f"job-worker:{job.hash_id}",
        )
        self._tasks.add(task)
        self._task_users[task] = job.user_id
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        user_id = self._task_users.pop(task, None)
        if user_id is not None:
            current = self._running_by_user.get(user_id, 0)
            if current <= 1:
                self._running_by_user.pop(user_id, None)
            else:
                self._running_by_user[user_id] = current - 1
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("scheduler worker task failed")
        if not self._tasks:
            self._drained.set()

    def _prune_done(self) -> None:
        for task in list(self._tasks):
            if task.done():
                self._on_task_done(task)

    async def _wait_for_tasks(self) -> None:
        if not self._tasks:
            self._drained.set()
            return
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._drained.set()

    def _has_free_worker_slot(self) -> bool:
        return len(self._tasks) < self._global_max_workers()

    def _user_has_slot(self, job: QueuedJob) -> bool:
        try:
            limit = get_tier_config().get(job.tier).max_concurrency
        except KeyError:
            limit = 1
        return self._running_by_user.get(job.user_id, 0) < max(1, int(limit))

    def _global_max_workers(self) -> int:
        try:
            return max(1, int(self._config.global_max_workers))
        except KeyError:
            return 32


async def restore_queued_jobs(queue: JobQueue | None = None) -> int:
    """Load durable QUEUED jobs into the in-memory queue at startup.

    The design doc says the in-memory queue is not durable, but restoring
    rows that are still marked QUEUED is a small operational courtesy during
    tests and local restarts. RUNNING recovery is intentionally left for
    PR-18's graceful-restart hardening.
    """
    q = queue or get_job_queue()
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job)
                .where(Job.status == "QUEUED")
                .order_by(Job.queued_at, Job.created_at)
            )
        ).scalars().all()
    for row in rows:
        await q.enqueue(QueuedJob.from_job(row))
    return len(rows)


async def list_provider_ids() -> Iterable[str]:
    async with get_session() as session:
        rows = (await session.execute(select(Provider.id))).scalars().all()
    return list(rows)


_instance: JobScheduler | None = None


def get_job_scheduler() -> JobScheduler:
    global _instance
    if _instance is None:
        _instance = JobScheduler()
    return _instance


def reset_job_scheduler_for_tests() -> None:
    global _instance
    _instance = None
