"""WFQ anti-starvation: Free lane gets ≥ 5% under sustained VIP load.

This is a JobQueue-level test that exercises the WFQ pop ordering
under continuous high load on every lane. It bypasses the executor
because we only care about *which lane* the scheduler picks each tick.

Per design doc §7.2 / §13.5 the configured ``min_share_per_lane`` is
0.05 (5%). With four lanes each containing more jobs than the dispatch
window can fit, a fairness-blind WFQ would still serve Free at the
weight ratio (1/15 ≈ 6.7%); the test asserts the floor explicitly so
a future config change that drops the weight to 0 doesn't regress us
silently.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.job_queue import QueuedJob, get_job_queue


def _job(tier: str, seq: int) -> QueuedJob:
    return QueuedJob(
        hash_id=f"j_{tier[:3]}{seq:09d}",
        job_id=f"job_{tier[:3]}{seq:09d}",
        user_id=f"u_{tier[:3]}",
        tier=tier,
        model="gpt-image-2",
        queued_at=datetime.now(timezone.utc),
        seq_no=seq,
    )


@pytest.mark.asyncio
async def test_free_lane_meets_5pct_minimum_share(initialized_db: None) -> None:
    """Push 100 jobs per lane and assert Free retains ≥ 5% of dispatches."""
    queue = get_job_queue()
    queue.clear()

    # Seed 100 of each tier so no lane drains during the sample window.
    # The continuous-load shape is what triggers the anti-starvation
    # guard; if we under-load Free the WFQ would simply finish it early.
    for seq in range(100):
        await queue.enqueue(_job("vip", seq))
        await queue.enqueue(_job("premium", seq))
        await queue.enqueue(_job("standard", seq))
        await queue.enqueue(_job("free", seq))

    # Pop 200 jobs and tally dispatches per tier. 200 is twice the
    # dispatch window, so the share floor has been "calibrated" by the
    # second half of the trace.
    counts: dict[str, int] = {"vip": 0, "premium": 0, "standard": 0, "free": 0}
    for _ in range(200):
        job = await queue.pop_next()
        assert job is not None
        counts[job.tier] += 1

    total = sum(counts.values())
    assert total == 200
    free_share = counts["free"] / total
    assert free_share >= 0.05, (
        f"Free lane share dropped below 5% floor: counts={counts}"
    )
    # Sanity: weights still dominate, so VIP > Premium > Standard > Free.
    assert counts["vip"] > counts["premium"]
    assert counts["premium"] > counts["standard"]


@pytest.mark.asyncio
async def test_wfq_uses_8_4_2_1_weight_ratio_in_steady_state(
    initialized_db: None,
) -> None:
    """The first 30 dispatches under uniform load follow the 8:4:2:1 weights.

    With weights 8/4/2/1 the WFQ virtual time advances by 1/weight per
    pop, so over 15 ticks the lanes serve {VIP: 8, Premium: 4, Standard: 2,
    Free: 1}. We sample 30 ticks (two cycles) so the assertion has slack
    against the anti-starvation guard kicking in late.
    """
    queue = get_job_queue()
    queue.clear()

    for seq in range(40):
        await queue.enqueue(_job("vip", seq))
        await queue.enqueue(_job("premium", seq))
        await queue.enqueue(_job("standard", seq))
        await queue.enqueue(_job("free", seq))

    counts: dict[str, int] = {"vip": 0, "premium": 0, "standard": 0, "free": 0}
    for _ in range(30):
        job = await queue.pop_next()
        assert job is not None
        counts[job.tier] += 1

    # WFQ allocations should be exactly 16/8/4/2 over 30 ticks of two
    # full cycles; allow ±1 for the fact that ties on virtual time can
    # break either way.
    assert abs(counts["vip"] - 16) <= 1
    assert abs(counts["premium"] - 8) <= 1
    assert abs(counts["standard"] - 4) <= 1
    assert abs(counts["free"] - 2) <= 1
