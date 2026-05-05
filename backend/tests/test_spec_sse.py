"""T-SSE-NN spec cases (文生图平台测试方案 §5.11).

Note: ``httpx.ASGITransport`` cannot cleanly tear down a long-lived
``StreamingResponse`` mid-flight (documented in test_sse_hub.py).  We
exercise the hub directly so the contract — first event is ``hello``,
events are isolated per user — stays covered.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest


pytestmark = [pytest.mark.sse]


async def _drain_one(iterator):
    return await asyncio.wait_for(iterator.__anext__(), timeout=2.0)


# ---------------------------------------------------------------------------
# T-SSE-01 · first frame is `hello` with `server_time`
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_sse_01_hello_first(seeded_app: httpx.AsyncClient):
    from app.domain.sse_hub import get_sse_hub

    hub = get_sse_hub()
    iterator = hub.stream("u_test_sse01____", last_event_id=None)
    chunk = await _drain_one(iterator)
    text = chunk.decode("utf-8")
    assert "event: hello" in text
    assert "server_time" in text
    await iterator.aclose()


# ---------------------------------------------------------------------------
# T-SSE-06 · cross-user isolation
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_sse_06_cross_user_isolation(seeded_app: httpx.AsyncClient):
    from app.domain.sse_hub import get_sse_hub

    hub = get_sse_hub()
    iter_a = hub.stream("u_a_iso", last_event_id=None)
    iter_b = hub.stream("u_b_iso", last_event_id=None)
    # Drain the hello on each
    await _drain_one(iter_a)
    await _drain_one(iter_b)
    await asyncio.sleep(0.05)

    # Broadcast event for A only
    await hub.broadcast_to_user(
        "u_a_iso",
        "task_created",
        {"hash_id": "j_aaaaaaaaaaab"},
    )
    a_chunk = await _drain_one(iter_a)
    assert b"task_created" in a_chunk
    assert b"j_aaaaaaaaaaab" in a_chunk

    # B should time out — no event for them
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(iter_b.__anext__(), timeout=0.5)

    await iter_a.aclose()
    await iter_b.aclose()
