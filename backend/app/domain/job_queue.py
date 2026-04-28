"""In-memory 4-lane WFQ queue for generation jobs.

This module is PR-11's queue layer. It deliberately stores only compact
snapshots of jobs; the executor reloads the durable row before doing any
side effects. The queue itself is process-local by design for v1
(single-worker uvicorn + SQLite), matching the design document's "restart
drops queued in-memory work" tradeoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Iterable

from sqlalchemy import select, update

from app.db.engine import get_session
from app.db.models import Job
from app.domain.runtime_configs import SchedulerConfig
from app.domain.tier_config import TierSpec, get_tier_config

logger = logging.getLogger("txt2img.job_queue")


TIER_ORDER: tuple[str, ...] = ("vip", "premium", "standard", "free")
PROMOTION_TARGET: dict[str, str] = {
    "free": "standard",
    "standard": "premium",
    "premium": "vip",
}
_TIER_RANK: dict[str, int] = {
    "vip": 3,
    "premium": 2,
    "standard": 1,
    "free": 0,
}
_DEFAULT_WEIGHTS: dict[str, int] = {
    "vip": 8,
    "premium": 4,
    "standard": 2,
    "free": 1,
}


class QueueClosed(RuntimeError):
    """Raised when enqueue is attempted after scheduler drain starts."""


@dataclass(frozen=True)
class QueuedJob:
    """Compact queue item.

    ``tier`` is the user's original tier at submit time and remains the
    quota/SSE tier. ``lane_tier`` is the effective scheduling lane, which can
    be one tier higher after deadline promotion. ``promoted`` is intentionally
    a queue-level flag; the DB ``flags_json`` is updated best-effort when the
    promotion happens.
    """

    hash_id: str
    job_id: str
    user_id: str
    tier: str
    model: str
    queued_at: datetime
    seq_no: int | None = None
    set_id: str | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    lane_tier: str | None = None
    promoted: bool = False

    def __post_init__(self) -> None:
        original = self.tier.lower()
        lane = (self.lane_tier or original).lower()
        object.__setattr__(self, "tier", original)
        object.__setattr__(self, "lane_tier", lane)

    @classmethod
    def from_job(cls, job: Job) -> "QueuedJob":
        """Build a queue item from a persisted ``jobs`` row."""
        return cls(
            hash_id=job.hash_id,
            job_id=job.id,
            user_id=job.user_id,
            tier=job.tier_at_submit,
            model=job.model,
            queued_at=_as_aware_utc(job.queued_at or job.created_at),
            seq_no=job.seq_no,
            set_id=job.set_id,
            flags=_safe_json_dict(job.flags_json),
            promoted=bool(_safe_json_dict(job.flags_json).get("promoted")),
        )


@dataclass
class Lane:
    """One FIFO lane in the WFQ scheduler."""

    tier: str
    weight: int
    queue: deque[QueuedJob] = field(default_factory=deque)
    virtual_time: Fraction = Fraction(0, 1)


class JobQueue:
    """Four FIFO lanes plus WFQ selection state."""

    def __init__(
        self,
        *,
        scheduler_config: SchedulerConfig | None = None,
    ) -> None:
        self._scheduler_config = scheduler_config or SchedulerConfig()
        self._lanes: dict[str, Lane] = {
            tier: Lane(tier=tier, weight=self._weight_for(tier))
            for tier in TIER_ORDER
        }
        self._lock = asyncio.Lock()
        self._closed = False
        # Recent dispatch tiers for the anti-starvation share check.
        self._dispatch_window: deque[str] = deque(maxlen=200)
        self._ticks = 0

    @property
    def lanes(self) -> dict[str, Lane]:
        """Return the live lanes for diagnostics/tests. Do not mutate."""
        return self._lanes

    async def enqueue(self, job: QueuedJob) -> int:
        """Append ``job`` to its effective lane and return total queue size."""
        async with self._lock:
            if self._closed:
                raise QueueClosed("job queue is draining; new jobs are rejected")
            return self._append_locked(job)

    async def requeue(self, job: QueuedJob) -> int:
        """Put ``job`` back at the tail of its current lane.

        Used when the scheduler popped a job but the user's concurrency
        ceiling is already full. This intentionally ignores ``_closed``:
        the job was already accepted before drain began.
        """
        async with self._lock:
            return self._append_locked(job)

    async def pop_next(self) -> QueuedJob | None:
        """Promote expired jobs, pop the next WFQ job, and advance vt."""
        async with self._lock:
            self._refresh_lane_weights()
            await self._promote_expired_locked()
            lane = self._pick_lane_locked()
            if lane is None:
                return None
            job = lane.queue.popleft()
            lane.virtual_time += Fraction(1, max(1, lane.weight))
            self._dispatch_window.append(lane.tier)
            self._ticks += 1
            if self._ticks % 10_000 == 0:
                self._normalize_virtual_times_locked()
            return job

    async def remove(self, hash_id: str) -> QueuedJob | None:
        """Remove a queued job by id. Used by the future cancel endpoint."""
        async with self._lock:
            for lane in self._lanes.values():
                for idx, job in enumerate(lane.queue):
                    if job.hash_id == hash_id:
                        del lane.queue[idx]
                        return job
            return None

    async def size(self) -> int:
        async with self._lock:
            return self.size_locked()

    def size_locked(self) -> int:
        return sum(len(lane.queue) for lane in self._lanes.values())

    async def queued_counts(self) -> dict[str, int]:
        async with self._lock:
            return {tier: len(lane.queue) for tier, lane in self._lanes.items()}

    async def position(self, hash_id: str) -> int | None:
        """Return an approximate WFQ position by simulating current lanes."""
        async with self._lock:
            queues = {
                tier: deque(lane.queue)
                for tier, lane in self._lanes.items()
            }
            vts = {tier: lane.virtual_time for tier, lane in self._lanes.items()}
            pos = 0
            while any(queues.values()):
                lane_tier = min(
                    (tier for tier, q in queues.items() if q),
                    key=lambda tier: (vts[tier], -_TIER_RANK[tier]),
                )
                job = queues[lane_tier].popleft()
                pos += 1
                if job.hash_id == hash_id:
                    return pos
                vts[lane_tier] += Fraction(
                    1, max(1, self._lanes[lane_tier].weight)
                )
            return None

    def close(self) -> None:
        self._closed = True

    def reopen_for_tests(self) -> None:
        self._closed = False

    def clear(self) -> None:
        """Drop all queued jobs and reset WFQ state. Tests only."""
        for lane in self._lanes.values():
            lane.queue.clear()
            lane.virtual_time = Fraction(0, 1)
        self._dispatch_window.clear()
        self._ticks = 0
        self._closed = False

    def _pick_lane_locked(self) -> Lane | None:
        nonempty = [lane for lane in self._lanes.values() if lane.queue]
        if not nonempty:
            return None

        starved = self._starved_lane_locked(nonempty)
        if starved is not None:
            return starved

        return min(
            nonempty,
            key=lambda lane: (lane.virtual_time, -_TIER_RANK[lane.tier]),
        )

    def _append_locked(self, job: QueuedJob) -> int:
        lane_tier = self._normalise_tier(job.lane_tier or job.tier)
        self._refresh_lane_weights()
        self._lanes[lane_tier].queue.append(replace(job, lane_tier=lane_tier))
        total = self.size_locked()
        logger.info(
            "queue: enqueued job=%s lane=%s total=%d",
            job.hash_id,
            lane_tier,
            total,
        )
        return total

    def _starved_lane_locked(self, nonempty: Iterable[Lane]) -> Lane | None:
        """Return a lane below the configured min share, if meaningful."""
        total = len(self._dispatch_window)
        # Do not disturb the deterministic initial WFQ cycle. The min-share
        # guard is for sustained extreme load, not the first handful of ticks.
        if total < 20:
            return None
        min_share = self._min_share()
        candidates: list[tuple[float, int, Lane]] = []
        counts = {
            tier: sum(1 for dispatched in self._dispatch_window if dispatched == tier)
            for tier in TIER_ORDER
        }
        for lane in nonempty:
            share = counts.get(lane.tier, 0) / total
            if share < min_share:
                # Lower tier gets preference when several lanes are starved;
                # the point of this guard is to protect low-tier throughput.
                candidates.append((share, _TIER_RANK[lane.tier], lane))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    async def _promote_expired_locked(self) -> None:
        deadline = self._deadline_seconds()
        if deadline <= 0:
            return
        now = datetime.now(timezone.utc)
        promotions: list[tuple[QueuedJob, str]] = []

        for tier in ("free", "standard", "premium"):
            lane = self._lanes[tier]
            kept: deque[QueuedJob] = deque()
            while lane.queue:
                job = lane.queue.popleft()
                waited = (now - _as_aware_utc(job.queued_at)).total_seconds()
                target = PROMOTION_TARGET.get(tier)
                if (
                    target is not None
                    and not job.promoted
                    and waited > deadline
                ):
                    promoted = replace(
                        job,
                        lane_tier=target,
                        promoted=True,
                        flags={**job.flags, "promoted": True},
                    )
                    promotions.append((promoted, target))
                else:
                    kept.append(job)
            lane.queue = kept

        for job, target in promotions:
            self._lanes[target].queue.append(job)
            await _mark_promoted(job.hash_id)
            logger.info(
                "queue: promoted job=%s from=%s to=%s",
                job.hash_id,
                job.tier,
                target,
            )

    def _normalize_virtual_times_locked(self) -> None:
        nonempty_vts = [
            lane.virtual_time for lane in self._lanes.values() if lane.queue
        ]
        if not nonempty_vts:
            for lane in self._lanes.values():
                lane.virtual_time = Fraction(0, 1)
            return
        baseline = min(nonempty_vts)
        for lane in self._lanes.values():
            lane.virtual_time -= baseline

    def _refresh_lane_weights(self) -> None:
        for tier, lane in self._lanes.items():
            lane.weight = self._weight_for(tier)

    def _weight_for(self, tier: str) -> int:
        try:
            spec: TierSpec = get_tier_config().get(tier)
            return max(1, int(spec.weight))
        except KeyError:
            return _DEFAULT_WEIGHTS[tier]

    def _deadline_seconds(self) -> int:
        try:
            return int(self._scheduler_config.deadline_promotion_seconds)
        except KeyError:
            return 300

    def _min_share(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._scheduler_config.min_share_per_lane)))
        except KeyError:
            return 0.05

    def _normalise_tier(self, tier: str) -> str:
        t = tier.lower()
        if t not in self._lanes:
            raise ValueError(f"unknown queue tier {tier!r}")
        return t


async def _mark_promoted(hash_id: str) -> None:
    """Best-effort DB flag update for deadline promotion."""
    async with get_session() as session:
        raw = (
            await session.execute(select(Job.flags_json).where(Job.hash_id == hash_id))
        ).scalar_one_or_none()
        if raw is None:
            return
        flags = _safe_json_dict(raw)
        if flags.get("promoted") is True:
            return
        flags["promoted"] = True
        await session.execute(
            update(Job)
            .where(Job.hash_id == hash_id)
            .values(flags_json=json.dumps(flags, separators=(",", ":")))
        )


def _safe_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_instance: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _instance
    if _instance is None:
        _instance = JobQueue()
    return _instance


def reset_job_queue_for_tests() -> None:
    global _instance
    _instance = None
