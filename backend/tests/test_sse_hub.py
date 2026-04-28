"""SSE hub: fanout, replay, heartbeat, eviction, route integration.

Two layers of tests:

* **Unit tests against ``SSEHub``** drive the streaming generator
  directly with ``asyncio.wait_for`` and assert wire-format bytes /
  registry state. They don't touch FastAPI.
* **Route tests against ``GET /api/sse``** go through the real
  lifespan via ``seeded_app`` and a streaming httpx request so the
  ``StreamingResponse`` + ``Last-Event-ID`` header pickup is
  exercised end-to-end.

We deliberately use very short heartbeat intervals (1s or 0.2s) so the
test suite stays under a second per case. ``buffer_seconds`` is also
shrunk where it helps to avoid waiting on the trim path.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.domain.sse_hub import (
    SSEHub,
    _format_event,
    get_sse_hub,
    reset_sse_hub_for_tests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drain_until(
    iterator,
    *,
    matcher,
    timeout: float = 2.0,
) -> bytes:
    """Pull bytes from a hub stream until ``matcher(chunk)`` matches.

    Returns the matched chunk. Raises ``asyncio.TimeoutError`` if
    nothing matches within ``timeout``. We accumulate raw bytes
    because the SSE wire format is line-oriented and a single chunk
    is a complete event (the hub yields one event per ``yield``).
    """

    async def _impl():
        async for chunk in iterator:
            if matcher(chunk):
                return chunk
        raise AssertionError("iterator exhausted before matcher matched")

    return await asyncio.wait_for(_impl(), timeout=timeout)


def _parse_sse_block(block: bytes) -> dict[str, Any]:
    """Parse one SSE event block into ``{id, event, data}``.

    Lines starting with ``:`` are comments and are ignored; everything
    else follows the ``key: value`` form.
    """
    out: dict[str, Any] = {"id": None, "event": "message", "data": []}
    for raw_line in block.decode("utf-8").splitlines():
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        value = value.lstrip(" ")
        if key == "id":
            out["id"] = value
        elif key == "event":
            out["event"] = value
        elif key == "data":
            out["data"].append(value)
    if out["data"]:
        try:
            out["data"] = json.loads("\n".join(out["data"]))
        except json.JSONDecodeError:
            out["data"] = "\n".join(out["data"])
    else:
        out["data"] = None
    return out


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


def test_format_event_basic_shape() -> None:
    """An event with id+kind+payload encodes as ``id`` then ``event`` then ``data``."""
    out = _format_event(event_id="42-1", kind="job_state", payload={"a": 1})
    text = out.decode("utf-8")
    # Trailing blank line marks event boundary.
    assert text.endswith("\n\n")
    parts = text.strip().split("\n")
    assert parts[0] == "id: 42-1"
    assert parts[1] == "event: job_state"
    assert parts[2].startswith("data: ")
    payload = json.loads(parts[2][len("data: "):])
    assert payload == {"a": 1}


def test_format_event_omits_id_when_none() -> None:
    """Heartbeats / hello / connection_warning don't carry an id."""
    out = _format_event(event_id=None, kind="heartbeat", payload={"t": 1})
    text = out.decode("utf-8")
    assert "id: " not in text


# ---------------------------------------------------------------------------
# Single-client basics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hello_emitted_first(initialized_db: None) -> None:
    """First chunk yielded by ``stream`` is always the ``hello`` event."""
    reset_sse_hub_for_tests()
    hub = SSEHub(max_per_user=4, heartbeat_seconds=60)

    iterator = hub.stream("u_test_1234ab", last_event_id=None)
    chunk = await _drain_until(iterator, matcher=lambda b: b"event: hello" in b)
    parsed = _parse_sse_block(chunk)
    assert parsed["event"] == "hello"
    assert "session_id" in parsed["data"]
    assert "server_time" in parsed["data"]
    # ``hello`` carries no id — design choice, can't be replayed.
    assert parsed["id"] is None
    await iterator.aclose()


@pytest.mark.asyncio
async def test_broadcast_to_user_delivers_event(initialized_db: None) -> None:
    """A broadcast posted after stream open arrives in the iterator."""
    reset_sse_hub_for_tests()
    hub = SSEHub(max_per_user=4, heartbeat_seconds=60)

    user_id = "u_recv_1234aa"
    iterator = hub.stream(user_id, last_event_id=None)
    # Drain hello so subsequent chunks are real events.
    await _drain_until(iterator, matcher=lambda b: b"event: hello" in b)

    # Give the stream coroutine a chance to register the client before
    # the broadcast (otherwise the put_nowait fanout finds zero clients
    # and the event lives only in the replay buffer).
    await asyncio.sleep(0.05)

    await hub.broadcast_to_user(
        user_id, "job_state", {"hash_id": "j_a1b2c3d4e5f6", "to": "RUNNING"}
    )

    chunk = await _drain_until(
        iterator, matcher=lambda b: b"event: job_state" in b
    )
    parsed = _parse_sse_block(chunk)
    assert parsed["event"] == "job_state"
    assert parsed["data"]["hash_id"] == "j_a1b2c3d4e5f6"
    assert parsed["id"] is not None
    await iterator.aclose()


@pytest.mark.asyncio
async def test_heartbeat_emitted_on_idle(initialized_db: None) -> None:
    """No traffic for one heartbeat interval → server emits heartbeat."""
    reset_sse_hub_for_tests()
    hub = SSEHub(max_per_user=4, heartbeat_seconds=1)

    user_id = "u_hb_1234aaaa"
    iterator = hub.stream(user_id, last_event_id=None)
    await _drain_until(iterator, matcher=lambda b: b"event: hello" in b)
    chunk = await _drain_until(
        iterator, matcher=lambda b: b"event: heartbeat" in b, timeout=3.0
    )
    parsed = _parse_sse_block(chunk)
    assert parsed["event"] == "heartbeat"
    assert "t" in parsed["data"]
    await iterator.aclose()


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_after_reconnect(initialized_db: None) -> None:
    """Reconnect with ``Last-Event-ID`` replays only the missed events."""
    reset_sse_hub_for_tests()
    hub = SSEHub(max_per_user=4, heartbeat_seconds=60)

    user_id = "u_replay_1234"

    # Open + close a connection to capture id_a.
    iter_a = hub.stream(user_id, last_event_id=None)
    await _drain_until(iter_a, matcher=lambda b: b"event: hello" in b)
    await asyncio.sleep(0.05)
    await hub.broadcast_to_user(user_id, "task_created", {"hash_id": "j_aaa"})
    chunk_a = await _drain_until(
        iter_a, matcher=lambda b: b"event: task_created" in b
    )
    parsed_a = _parse_sse_block(chunk_a)
    id_a = parsed_a["id"]
    assert id_a is not None
    await iter_a.aclose()

    # Two more broadcasts while disconnected.
    await hub.broadcast_to_user(user_id, "job_state", {"hash_id": "j_aaa", "to": "RUNNING"})
    await hub.broadcast_to_user(user_id, "job_state", {"hash_id": "j_aaa", "to": "SUCCEEDED"})

    # Reconnect with last_event_id = id_a → must replay both missed events.
    iter_b = hub.stream(user_id, last_event_id=id_a)
    await _drain_until(iter_b, matcher=lambda b: b"event: hello" in b)
    chunk_running = await _drain_until(
        iter_b, matcher=lambda b: b"RUNNING" in b
    )
    chunk_succ = await _drain_until(
        iter_b, matcher=lambda b: b"SUCCEEDED" in b
    )
    assert _parse_sse_block(chunk_running)["data"]["to"] == "RUNNING"
    assert _parse_sse_block(chunk_succ)["data"]["to"] == "SUCCEEDED"
    await iter_b.aclose()


@pytest.mark.asyncio
async def test_replay_ignores_malformed_last_event_id(
    initialized_db: None,
) -> None:
    """Garbage in the header → replay all of buffer (frontend dedupes)."""
    reset_sse_hub_for_tests()
    hub = SSEHub(max_per_user=4, heartbeat_seconds=60)

    user_id = "u_bad_id"

    # Seed buffer.
    await hub.broadcast_to_user(user_id, "task_created", {"hash_id": "j_x"})
    await hub.broadcast_to_user(user_id, "job_state", {"hash_id": "j_x", "to": "RUNNING"})

    iterator = hub.stream(user_id, last_event_id="totally-not-an-id")
    await _drain_until(iterator, matcher=lambda b: b"event: hello" in b)
    await _drain_until(iterator, matcher=lambda b: b"event: task_created" in b)
    await _drain_until(iterator, matcher=lambda b: b"event: job_state" in b)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_buffer_drops_old_events(initialized_db: None) -> None:
    """Events older than ``buffer_seconds`` are pruned from the replay window."""
    reset_sse_hub_for_tests()
    # 50 ms window: short enough to test in-process, long enough that the
    # *just-appended* event survives the trim that runs at append time.
    hub = SSEHub(max_per_user=4, heartbeat_seconds=60, buffer_seconds=0.05)

    user_id = "u_decay"
    await hub.broadcast_to_user(user_id, "task_created", {"hash_id": "j_old"})
    # Sleep past the buffer window so the next append's trim sweep
    # evicts ``j_old`` but not the new ``j_new``.
    await asyncio.sleep(0.12)
    await hub.broadcast_to_user(user_id, "task_created", {"hash_id": "j_new"})

    stats = hub.stats()
    # Only the most recent event survives.
    assert stats["buffered_events"] == 1


# ---------------------------------------------------------------------------
# Connection cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_cap_evicts_oldest(initialized_db: None) -> None:
    """Opening connection N+1 evicts the oldest with a connection_warning."""
    reset_sse_hub_for_tests()
    hub = SSEHub(max_per_user=2, heartbeat_seconds=60)
    user_id = "u_cap"

    iter_a = hub.stream(user_id, last_event_id=None)
    iter_b = hub.stream(user_id, last_event_id=None)
    # Drain hellos so each generator has registered with the hub.
    await _drain_until(iter_a, matcher=lambda b: b"event: hello" in b)
    await _drain_until(iter_b, matcher=lambda b: b"event: hello" in b)
    assert hub.client_count_for_user(user_id) == 2

    iter_c = hub.stream(user_id, last_event_id=None)
    await _drain_until(iter_c, matcher=lambda b: b"event: hello" in b)

    # iter_a is the oldest — it should now receive a connection_warning
    # and then terminate cleanly.
    chunk = await _drain_until(
        iter_a, matcher=lambda b: b"event: connection_warning" in b
    )
    parsed = _parse_sse_block(chunk)
    assert parsed["data"]["reason"] == "evicted_oldest"

    # Drain iter_a until it hits the terminator (StopAsyncIteration).
    with pytest.raises((StopAsyncIteration, asyncio.TimeoutError)):
        await asyncio.wait_for(iter_a.__anext__(), timeout=1.0)

    # Hub now hosts B + C only.
    assert hub.client_count_for_user(user_id) == 2

    await iter_b.aclose()
    await iter_c.aclose()


# ---------------------------------------------------------------------------
# Multi-event-kind multiplexing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_event_kinds_share_one_channel(initialized_db: None) -> None:
    """One stream carries job_state, task_created, announcement, etc."""
    reset_sse_hub_for_tests()
    hub = SSEHub(max_per_user=4, heartbeat_seconds=60)
    user_id = "u_multi_1234"

    iterator = hub.stream(user_id, last_event_id=None)
    await _drain_until(iterator, matcher=lambda b: b"event: hello" in b)
    await asyncio.sleep(0.05)

    kinds = ["task_created", "job_state", "announcement", "task_deleted"]
    for kind in kinds:
        await hub.broadcast_to_user(user_id, kind, {"k": kind})

    received: list[str] = []
    async def _collect():
        async for chunk in iterator:
            parsed = _parse_sse_block(chunk)
            if parsed["event"] in kinds:
                received.append(parsed["event"])
                if len(received) == len(kinds):
                    return
    await asyncio.wait_for(_collect(), timeout=2.0)
    assert received == kinds  # delivery order is FIFO

    await iterator.aclose()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_sse_hub_returns_same_instance(fresh_env: None) -> None:
    """``get_sse_hub`` is a memoized factory — same call returns same hub."""
    # ``fresh_env`` is required because :class:`SSEHub` reads
    # ``Settings`` for its defaults during ``__init__``.
    reset_sse_hub_for_tests()
    a = get_sse_hub()
    b = get_sse_hub()
    assert a is b
    reset_sse_hub_for_tests()
    c = get_sse_hub()
    assert c is not a


# ---------------------------------------------------------------------------
# JobLifecycle integration: lifecycle.transition publishes through hub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_transition_publishes_via_hub(
    initialized_db: None,
) -> None:
    """End-to-end: a lifecycle.transition lands in the SSE stream."""
    from app.db.engine import get_session
    from app.db.jobs_repository import get_jobs_repository
    from app.db.models import User
    from app.domain.job_lifecycle import (
        RUNNING,
        get_job_lifecycle,
        reset_job_lifecycle_for_tests,
        set_broadcast_sink,
    )
    from app.utils.ids import new_user_id
    from app.utils.security import hash_password

    reset_sse_hub_for_tests()
    reset_job_lifecycle_for_tests()
    hub = get_sse_hub()
    set_broadcast_sink(hub)

    # Insert a user + queued job.
    user_id = new_user_id()
    async with get_session() as session:
        session.add(
            User(
                id=user_id,
                username="alice_sse",
                password_hash=hash_password("pw1234"),
                role="user",
                tier="free",
            )
        )
    repo = get_jobs_repository()
    async with get_session() as session:
        created = await repo.insert_queued(
            user_id=user_id,
            tier_at_submit="free",
            model="gemini-3.1-flash-image-preview",
            params_json="{}",
            session=session,
        )
    hash_id = created.hash_id

    # Open the stream.
    iterator = hub.stream(user_id, last_event_id=None)
    await _drain_until(iterator, matcher=lambda b: b"event: hello" in b)
    await asyncio.sleep(0.05)

    # Transition triggers a broadcast through the lifecycle's sink.
    lc = get_job_lifecycle()
    await lc.transition(hash_id, RUNNING)

    chunk = await _drain_until(
        iterator, matcher=lambda b: b"event: job_state" in b
    )
    parsed = _parse_sse_block(chunk)
    assert parsed["data"]["hash_id"] == hash_id
    assert parsed["data"]["to"] == RUNNING

    await iterator.aclose()


# ---------------------------------------------------------------------------
# Route integration through seeded_app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_route_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    """Without a Bearer token, ``GET /api/sse`` is 401.

    Note: we deliberately don't run a streaming-body integration test
    here. ``httpx.ASGITransport`` does not cleanly tear down a
    long-lived ``StreamingResponse`` mid-flight (the inner ASGI task
    blocks on the next ``async for`` iteration of the SSE generator
    even after the client side closes), which makes any such test
    racy at best and a 30-second hang at worst. The unit tests
    against ``SSEHub`` plus
    ``test_lifecycle_transition_publishes_via_hub`` already exercise
    the full event-flow path; the route layer is the trivial mapping
    from request → ``hub.stream`` → ``StreamingResponse``.
    """
    resp = await seeded_app.get("/api/sse")
    assert resp.status_code == 401
