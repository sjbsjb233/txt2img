"""Cross-device SSE: device A submits, device B sees it (design doc §9.5).

Both clients are SSEHub.stream iterators on the same user_id; we don't
go through ``GET /api/sse`` because that needs a streaming HTTP layer
which httpx + ASGITransport does not exercise reliably under pytest.
The hub is the single source of truth for fanout — testing it at the
broadcast level is enough to lock in the cross-device contract.

We assert two things:

1. After Device A submits, **both** Device A's and Device B's hub
   streams receive a ``task_created`` event.
2. The lifecycle ``QUEUED → SUCCEEDED`` transition fans out to both
   streams as ``job_state`` events.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx
import pytest

from app.domain.sse_hub import SSEHub, get_sse_hub

from .conftest import (
    auth_header,
    job_payload,
    login_user,
    run_scheduler_until_idle,
    seed_provider,
    submit_job,
    wait_for_status,
)


async def _read_until(
    iterator: AsyncIterator[bytes],
    *,
    target_event: str,
    timeout: float = 5.0,
) -> dict:
    """Pull bytes until we see ``event: <target_event>``.

    Returns the parsed payload dict.
    """

    async def _impl() -> dict:
        buf = bytearray()
        async for chunk in iterator:
            buf.extend(chunk)
            blocks = buf.split(b"\n\n")
            buf = bytearray(blocks[-1])
            for block in blocks[:-1]:
                event_kind = "message"
                data_lines: list[str] = []
                for raw in block.decode("utf-8").splitlines():
                    if raw.startswith(":") or not raw:
                        continue
                    key, _, value = raw.partition(":")
                    value = value.lstrip(" ")
                    if key == "event":
                        event_kind = value
                    elif key == "data":
                        data_lines.append(value)
                if event_kind == target_event:
                    if data_lines:
                        return json.loads("\n".join(data_lines))
                    return {}
        raise AssertionError(f"stream ended before seeing {target_event!r}")

    return await asyncio.wait_for(_impl(), timeout=timeout)


@pytest.mark.asyncio
async def test_two_devices_see_task_created_for_same_user(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    await seed_provider(seeded_app, provider_id="oai_xd", label="XD")
    token = await login_user(seeded_app, username="xd_user")

    hub: SSEHub = get_sse_hub()
    user_id = (
        await seeded_app.get("/api/me", headers=auth_header(token))
    ).json()["id"]

    device_a = hub.stream(user_id)
    device_b = hub.stream(user_id)

    # Submit from "device A". Both devices must receive the
    # task_created broadcast.
    submit = asyncio.create_task(submit_job(seeded_app, token))

    seen_a = await _read_until(device_a, target_event="task_created")
    seen_b = await _read_until(device_b, target_event="task_created")

    resp = await submit
    assert resp.status_code == 200
    expected_hash = resp.json()["hash_id"]
    assert seen_a["hash_id"] == expected_hash
    assert seen_b["hash_id"] == expected_hash


@pytest.mark.asyncio
async def test_two_devices_see_job_state_succeeded(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    await seed_provider(seeded_app, provider_id="oai_xs", label="XS")
    token = await login_user(seeded_app, username="xs_user")
    user_id = (
        await seeded_app.get("/api/me", headers=auth_header(token))
    ).json()["id"]

    hub = get_sse_hub()
    device_a = hub.stream(user_id)
    device_b = hub.stream(user_id)

    resp = await submit_job(seeded_app, token)
    assert resp.status_code == 200
    hash_id = resp.json()["hash_id"]

    # Drive the scheduler to completion in the background.
    drive = asyncio.create_task(run_scheduler_until_idle(seeded_app, timeout=10))

    async def _wait_for_terminal(stream: AsyncIterator[bytes]) -> dict:
        # Read events until we see job_state with to=SUCCEEDED for our hash.
        deadline = asyncio.get_event_loop().time() + 10
        buf = bytearray()
        async for chunk in stream:
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError("timeout waiting for SUCCEEDED")
            buf.extend(chunk)
            blocks = buf.split(b"\n\n")
            buf = bytearray(blocks[-1])
            for block in blocks[:-1]:
                ev = "message"
                data: list[str] = []
                for raw in block.decode("utf-8").splitlines():
                    if not raw or raw.startswith(":"):
                        continue
                    k, _, v = raw.partition(":")
                    v = v.lstrip(" ")
                    if k == "event":
                        ev = v
                    elif k == "data":
                        data.append(v)
                if ev == "job_state" and data:
                    payload = json.loads("\n".join(data))
                    if (
                        payload.get("hash_id") == hash_id
                        and payload.get("to") == "SUCCEEDED"
                    ):
                        return payload
        raise AssertionError("stream ended before SUCCEEDED")

    try:
        a_event, b_event = await asyncio.gather(
            _wait_for_terminal(device_a), _wait_for_terminal(device_b)
        )
    finally:
        await drive

    assert a_event["hash_id"] == hash_id
    assert b_event["hash_id"] == hash_id
    assert a_event["to"] == "SUCCEEDED"
    assert b_event["to"] == "SUCCEEDED"
