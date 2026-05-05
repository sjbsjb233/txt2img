"""Minimal async SSE collector for httpx-based tests."""

from __future__ import annotations

import json
from typing import AsyncIterator


async def collect_sse(
    client,
    path: str,
    token: str,
    *,
    until_event: str | None = None,
    max_events: int = 50,
    timeout: float = 5.0,
):
    """Open an SSE stream, yield (event, data) pairs until limit / target."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    out: list[tuple[str, dict]] = []
    pending_event: str | None = None
    async with client.stream("GET", path, headers=headers, timeout=timeout) as resp:
        if resp.status_code != 200:
            return out
        async for line in resp.aiter_lines():
            line = line.rstrip("\r")
            if not line:
                continue
            if line.startswith("event:"):
                pending_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                try:
                    data = json.loads(payload)
                except ValueError:
                    data = {"raw": payload}
                out.append((pending_event or "message", data))
                pending_event = None
                if until_event and out[-1][0] == until_event:
                    return out
                if len(out) >= max_events:
                    return out
    return out
