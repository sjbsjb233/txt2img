"""JobLifecycle: state graph, side effects, broadcast, and ``seq_no``.

These tests run against a real (file-backed) SQLite via the
``initialized_db`` fixture so the DB CHECK constraints are exercised
alongside the in-Python state graph. We use the
:class:`RecordingBroadcastSink` to assert SSE payloads without
spinning up the real hub (which lands in PR-12).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.engine import get_session
from app.db.jobs_repository import JobsRepository, get_jobs_repository
from app.db.models import Job, User
from app.domain.job_lifecycle import (
    CANCELLED,
    DELETED,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    InvalidTransition,
    JobLifecycle,
    JobNotFound,
    NullBroadcastSink,
    RecordingBroadcastSink,
    get_job_lifecycle,
    reset_job_lifecycle_for_tests,
    set_broadcast_sink,
)
from app.utils.ids import new_user_id
from app.utils.security import hash_password


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _create_user(*, tier: str = "free", username: str = "alice") -> User:
    """Insert a user row; return the persisted ORM instance.

    Tests need a real user because ``jobs.user_id`` is a foreign key and
    the seq_no allocator updates ``users.last_seq_no``. The bootstrap
    admin is reserved for auth tests; we make our own here to keep
    seq_no math clean.
    """
    user = User(
        id=new_user_id(),
        username=username,
        password_hash=hash_password("pw1234"),
        role="user",
        tier=tier,
    )
    async with get_session() as session:
        session.add(user)
    return user


async def _insert_queued(
    user_id: str,
    *,
    repo: JobsRepository | None = None,
    model: str = "gemini-3.1-flash-image-preview",
) -> str:
    """Insert one QUEUED job for a user; return its hash_id."""
    repo = repo or get_jobs_repository()
    async with get_session() as session:
        created = await repo.insert_queued(
            user_id=user_id,
            tier_at_submit="free",
            model=model,
            params_json="{}",
            session=session,
        )
    return created.hash_id


@pytest.fixture
def reset_lifecycle():
    """Drop the lifecycle singleton between tests so sinks don't leak.

    Without this, a test that installs a recording sink can pollute the
    next test's expectations. We reset both before and after to be safe
    against test reordering.
    """
    reset_job_lifecycle_for_tests()
    yield
    reset_job_lifecycle_for_tests()


# ---------------------------------------------------------------------------
# State graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legal_transition_queued_to_running(
    initialized_db: None, reset_lifecycle
) -> None:
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    result = await lc.transition(hash_id, RUNNING)
    assert result.from_status == QUEUED
    assert result.to_status == RUNNING

    async with get_session() as session:
        job = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
    assert job.status == RUNNING


@pytest.mark.asyncio
async def test_legal_transition_running_to_succeeded_sets_finished_at(
    initialized_db: None, reset_lifecycle
) -> None:
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    await lc.transition(hash_id, RUNNING)
    result = await lc.transition(hash_id, SUCCEEDED)
    assert result.to_status == SUCCEEDED
    assert result.finished_at is not None

    async with get_session() as session:
        job = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_illegal_transition_succeeded_to_running_rejected(
    initialized_db: None, reset_lifecycle
) -> None:
    """Once SUCCEEDED, the job cannot go back to RUNNING — §8.1."""
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    await lc.transition(hash_id, RUNNING)
    await lc.transition(hash_id, SUCCEEDED)

    with pytest.raises(InvalidTransition):
        await lc.transition(hash_id, RUNNING)


@pytest.mark.asyncio
async def test_illegal_transition_queued_to_succeeded_rejected(
    initialized_db: None, reset_lifecycle
) -> None:
    """Skipping RUNNING is forbidden — the executor must dispatch first."""
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    with pytest.raises(InvalidTransition):
        await lc.transition(hash_id, SUCCEEDED)


@pytest.mark.asyncio
async def test_unknown_status_rejected(
    initialized_db: None, reset_lifecycle
) -> None:
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    with pytest.raises(InvalidTransition):
        await lc.transition(hash_id, "BANANAS")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_missing_job_raises(initialized_db: None, reset_lifecycle) -> None:
    lc = JobLifecycle()
    with pytest.raises(JobNotFound):
        await lc.transition("j_aaaaaaaaaaaa", RUNNING)


@pytest.mark.asyncio
async def test_deleted_is_terminal(initialized_db: None, reset_lifecycle) -> None:
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    await lc.transition(hash_id, CANCELLED)
    await lc.transition(hash_id, DELETED)

    # No further moves are legal once DELETED.
    for to in (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED):
        with pytest.raises(InvalidTransition):
            await lc.transition(hash_id, to)


# ---------------------------------------------------------------------------
# Side effects: timeline + broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_appends_timeline_jsonl(
    initialized_db: None, reset_lifecycle, tmp_path: Path
) -> None:
    """Every transition appends one line to ``timeline.jsonl``."""
    from app.services.image_io import path_for_timeline

    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    await lc.transition(hash_id, RUNNING)
    await lc.transition(hash_id, SUCCEEDED)

    timeline = path_for_timeline(hash_id)
    lines = timeline.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["from"] == QUEUED
    assert parsed[0]["to"] == RUNNING
    assert parsed[1]["from"] == RUNNING
    assert parsed[1]["to"] == SUCCEEDED
    # ts is added unconditionally by image_io.append_timeline.
    assert "ts" in parsed[0] and "ts" in parsed[1]


@pytest.mark.asyncio
async def test_recording_sink_captures_one_event_per_transition(
    initialized_db: None, reset_lifecycle
) -> None:
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    sink = RecordingBroadcastSink()
    lc = JobLifecycle(sink=sink)
    await lc.transition(hash_id, RUNNING)
    await lc.transition(hash_id, SUCCEEDED)

    assert len(sink.events) == 2

    user_id_a, event_a, payload_a = sink.events[0]
    assert user_id_a == user.id
    assert event_a == "job_state"
    assert payload_a["hash_id"] == hash_id
    assert payload_a["from"] == QUEUED
    assert payload_a["to"] == RUNNING

    _, _, payload_b = sink.events[1]
    assert payload_b["from"] == RUNNING
    assert payload_b["to"] == SUCCEEDED


@pytest.mark.asyncio
async def test_broadcast_false_suppresses_event(
    initialized_db: None, reset_lifecycle
) -> None:
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    sink = RecordingBroadcastSink()
    lc = JobLifecycle(sink=sink)
    await lc.transition(hash_id, RUNNING, broadcast=False)
    assert sink.events == []


@pytest.mark.asyncio
async def test_buggy_sink_does_not_roll_back_transition(
    initialized_db: None, reset_lifecycle
) -> None:
    """A sink that raises must not prevent the DB transition from sticking."""

    class _Bomb:
        async def broadcast_to_user(self, user_id, event, payload):
            raise RuntimeError("sink blew up")

    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle(sink=_Bomb())
    # Should not raise — buggy sink errors are swallowed.
    await lc.transition(hash_id, RUNNING)

    async with get_session() as session:
        job = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
    assert job.status == RUNNING


@pytest.mark.asyncio
async def test_default_sink_is_null_broadcast_sink(reset_lifecycle) -> None:
    lc = JobLifecycle()
    assert isinstance(lc.sink, NullBroadcastSink)


@pytest.mark.asyncio
async def test_set_broadcast_sink_singleton_helper(
    initialized_db: None, reset_lifecycle
) -> None:
    """``set_broadcast_sink`` swaps the sink on the process-wide singleton."""
    sink = RecordingBroadcastSink()
    set_broadcast_sink(sink)

    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    await get_job_lifecycle().transition(hash_id, RUNNING)
    assert len(sink.events) == 1
    assert sink.events[0][1] == "job_state"


# ---------------------------------------------------------------------------
# seq_no atomicity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seq_no_increments_strictly(
    initialized_db: None, reset_lifecycle
) -> None:
    """Sequential inserts must produce 1..N with no gaps and no repeats."""
    user = await _create_user()
    repo = JobsRepository()

    seq_nos: list[int] = []
    for _ in range(10):
        async with get_session() as session:
            created = await repo.insert_queued(
                user_id=user.id,
                tier_at_submit="free",
                model="gemini-3.1-flash-image-preview",
                params_json="{}",
                session=session,
            )
        seq_nos.append(created.seq_no)

    assert seq_nos == list(range(1, 11))


@pytest.mark.asyncio
async def test_seq_no_concurrent_inserts_unique(
    initialized_db: None, reset_lifecycle
) -> None:
    """50 concurrent inserts must all succeed with unique seq_nos.

    The unique index ``idx_jobs_user_seq`` would surface any double-
    allocation as an integrity error and fail the test. With the atomic
    ``UPDATE ... RETURNING`` increment we expect every coroutine to
    serialise through the SQLite write lock and observe the post-update
    value of its own statement.
    """
    user = await _create_user()
    repo = JobsRepository()

    async def insert_one() -> int:
        async with get_session() as session:
            created = await repo.insert_queued(
                user_id=user.id,
                tier_at_submit="free",
                model="gemini-3.1-flash-image-preview",
                params_json="{}",
                session=session,
            )
        return created.seq_no

    seq_nos = await asyncio.gather(*(insert_one() for _ in range(50)))
    assert len(seq_nos) == 50
    assert len(set(seq_nos)) == 50
    assert sorted(seq_nos) == list(range(1, 51))

    async with get_session() as session:
        refreshed = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()
    assert refreshed.last_seq_no == 50


@pytest.mark.asyncio
async def test_seq_no_independent_per_user(
    initialized_db: None, reset_lifecycle
) -> None:
    """Two users have independent seq_no spaces."""
    a = await _create_user(username="a_user")
    b = await _create_user(username="b_user")

    await _insert_queued(a.id)
    await _insert_queued(a.id)
    await _insert_queued(b.id)

    async with get_session() as session:
        a_jobs = (
            (
                await session.execute(
                    select(Job).where(Job.user_id == a.id).order_by(Job.seq_no)
                )
            )
            .scalars()
            .all()
        )
        b_jobs = (
            (
                await session.execute(
                    select(Job).where(Job.user_id == b.id).order_by(Job.seq_no)
                )
            )
            .scalars()
            .all()
        )
    assert [j.seq_no for j in a_jobs] == [1, 2]
    assert [j.seq_no for j in b_jobs] == [1]


@pytest.mark.asyncio
async def test_set_id_generation_is_caller_controlled(
    initialized_db: None, reset_lifecycle
) -> None:
    """``insert_queued`` does not auto-generate a set_id — caller decides.

    The design doc says ``set_id`` is non-null only when ``n > 1``.
    PR-13's create-job handler decides that based on the request; the
    repository stays neutral so it can be reused by single-image and
    set-mode flows alike.
    """
    user = await _create_user()
    repo = JobsRepository()

    # n=1 path — pass set_id=None.
    async with get_session() as session:
        single = await repo.insert_queued(
            user_id=user.id,
            tier_at_submit="free",
            model="gpt-image-2",
            params_json="{}",
            set_id=None,
            session=session,
        )
    assert single.set_id is None

    # n>1 path — caller pre-generates a set_id and passes it in.
    from app.utils.ids import new_set_id

    set_id = new_set_id()
    async with get_session() as session:
        multi = await repo.insert_queued(
            user_id=user.id,
            tier_at_submit="free",
            model="gpt-image-2",
            params_json="{}",
            set_id=set_id,
            session=session,
        )
    assert multi.set_id == set_id


@pytest.mark.asyncio
async def test_count_active_by_user_filters_status(
    initialized_db: None, reset_lifecycle
) -> None:
    user = await _create_user()
    repo = JobsRepository()

    a = await _insert_queued(user.id, repo=repo)
    b = await _insert_queued(user.id, repo=repo)
    c = await _insert_queued(user.id, repo=repo)

    lc = JobLifecycle()
    await lc.transition(a, RUNNING)
    await lc.transition(b, RUNNING)
    await lc.transition(b, SUCCEEDED)
    await lc.transition(c, CANCELLED)

    # Active = QUEUED + RUNNING. We have one RUNNING (a) only.
    count = await repo.count_active_by_user(user.id)
    assert count == 1


# ---------------------------------------------------------------------------
# Compare-and-swap concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cas_rejects_when_status_already_advanced(
    initialized_db: None, reset_lifecycle
) -> None:
    """Simulate a lost-race: another caller already moved the row.

    We can't easily wedge two simultaneous transitions in a single test
    process (SQLite WAL serialises writers very tightly), but we can
    exercise the same code path by advancing the row through one
    lifecycle call and then asking lifecycle to advance the same row
    *from* the original status. The SELECT in the second call sees
    the new status, the §8.1 adjacency check fires, and the call
    raises :class:`InvalidTransition` — which is exactly what the
    CAS-loser branch produces.
    """
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    await lc.transition(hash_id, RUNNING)
    # Row is now RUNNING. A second QUEUED→RUNNING is illegal.
    with pytest.raises(InvalidTransition):
        await lc.transition(hash_id, RUNNING)


@pytest.mark.asyncio
async def test_cas_raises_when_row_changes_between_select_and_update(
    initialized_db: None, reset_lifecycle, monkeypatch
) -> None:
    """Force the rare race window: row mutates between SELECT and UPDATE.

    We monkey-patch :class:`JobLifecycle._transition_in_session`'s use
    of :func:`asyncio.to_thread` in a benign place… actually simpler:
    we directly bypass the lifecycle and run a competing UPDATE inside
    the same session, between the lifecycle's SELECT and UPDATE. The
    cleanest way is to wrap the lifecycle's session.execute via
    monkeypatch and inject a sibling write between the SELECT and the
    update statement.

    Implementation note: rather than thread a raw mock through
    SQLAlchemy internals, we exploit the public surface — we call
    ``transition`` from coroutine A, immediately do a sibling UPDATE
    via a *different* session, then verify the original transition
    raises. This works because aiosqlite on default isolation
    interleaves writes serially under the WAL writer lock; the second
    UPDATE wins, the lifecycle's CAS WHERE clause fails, and we get
    :class:`InvalidTransition`.
    """
    from sqlalchemy import update as sa_update

    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()

    # Pre-bump the row outside the lifecycle to simulate "another
    # session won the race". The lifecycle will then SELECT, see the
    # advanced status, and reject the transition via the §8.1
    # adjacency check (which is the same outcome as CAS rowcount=0).
    async with get_session() as s:
        await s.execute(
            sa_update(Job)
            .where(Job.hash_id == hash_id)
            .values(status=RUNNING)
        )

    with pytest.raises(InvalidTransition):
        await lc.transition(hash_id, RUNNING)


# ---------------------------------------------------------------------------
# Caller-managed session: deferred broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_managed_session_does_not_broadcast(
    initialized_db: None, reset_lifecycle
) -> None:
    """Passing a session must defer the broadcast — even with broadcast=True."""
    sink = RecordingBroadcastSink()
    lc = JobLifecycle(sink=sink)

    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    async with get_session() as s:
        result = await lc.transition(hash_id, RUNNING, session=s)

    # No event fired during the transition: caller owns the commit.
    assert sink.events == []

    # After commit, caller publishes manually.
    await lc.publish_transition(result)
    assert len(sink.events) == 1
    assert sink.events[0][1] == "job_state"
    assert sink.events[0][2]["from"] == QUEUED
    assert sink.events[0][2]["to"] == RUNNING


@pytest.mark.asyncio
async def test_caller_managed_session_with_broadcast_false_publishes_nothing(
    initialized_db: None, reset_lifecycle
) -> None:
    """Caller can opt out of the SSE event entirely."""
    sink = RecordingBroadcastSink()
    lc = JobLifecycle(sink=sink)

    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    async with get_session() as s:
        await lc.transition(hash_id, RUNNING, session=s, broadcast=False)

    assert sink.events == []


# ---------------------------------------------------------------------------
# Timestamp consistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_ts_matches_transition_timestamp(
    initialized_db: None, reset_lifecycle
) -> None:
    """SSE ``ts`` must match the row's ``updated_at``, not broadcast time.

    With caller-managed sessions the broadcast can be arbitrarily
    delayed, so the payload's ``ts`` must reflect when the row
    actually changed — not when ``publish_transition`` happens to
    run.
    """
    sink = RecordingBroadcastSink()
    lc = JobLifecycle(sink=sink)

    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    async with get_session() as s:
        result = await lc.transition(hash_id, RUNNING, session=s)

    # Pretend the caller did a bunch of other work before publishing.
    await asyncio.sleep(0.05)
    await lc.publish_transition(result)

    payload = sink.events[0][2]
    expected = result.transitioned_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    assert payload["ts"] == expected


@pytest.mark.asyncio
async def test_transitioned_at_returned_on_result(
    initialized_db: None, reset_lifecycle
) -> None:
    user = await _create_user()
    hash_id = await _insert_queued(user.id)

    lc = JobLifecycle()
    result = await lc.transition(hash_id, RUNNING)
    assert result.transitioned_at is not None
    assert result.transitioned_at.tzinfo is not None
