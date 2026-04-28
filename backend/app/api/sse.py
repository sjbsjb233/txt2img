"""SSE long-poll endpoint.

A single ``GET /api/sse`` connection per browser tab. Multiplexes every
event kind the design doc enumerates (§8.5.1) — ``hello``,
``heartbeat``, ``job_state``, ``job_progress``, ``task_created``,
``task_deleted``, ``announcement``, ``model_capabilities_changed``,
``connection_warning`` — so the frontend stays under the browser's
6-connections-per-origin SSE cap regardless of how many tabs are open.

Auth: standard Bearer token via the ``Authorization`` header. The native
``EventSource`` API can't carry custom headers, so the frontend uses
``fetch`` with a streaming body reader (see ``frontend/src/api/sse.js``).

Reconnect: clients send the last id they saw via ``Last-Event-ID``. The
hub replays anything strictly newer that's still in its 5-minute buffer
before resuming live delivery.

Heartbeat: the hub itself emits ``: ping`` comment lines + ``heartbeat``
events every ``SSE_HEARTBEAT_SECONDS`` from inside the per-client
generator, so the route doesn't run a separate ticker.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from app.deps import CurrentUser
from app.domain.sse_hub import get_sse_hub

logger = logging.getLogger("txt2img.api.sse")

router = APIRouter(prefix="/api", tags=["sse"])


@router.get("/sse")
async def sse_stream(
    request: Request,
    user: CurrentUser,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Open one SSE channel for the authenticated user.

    The handler returns a ``StreamingResponse`` whose body is the
    iterator the hub builds on a per-client basis. Cancellation is
    automatic: when the client TCP-closes, Starlette cancels the task
    that's iterating, which the hub catches and uses to drop the
    client from its registry.

    Headers:
      * ``Content-Type: text/event-stream`` — the SSE wire format.
      * ``Cache-Control: no-cache`` — proxies must not cache.
      * ``X-Accel-Buffering: no`` — nginx-specific; disables response
        buffering so each event reaches the browser immediately.
      * ``Connection: keep-alive`` — explicit because some HTTP/1.1
        intermediaries default to closing.
    """
    hub = get_sse_hub()
    iterator = hub.stream(user.id, last_event_id=last_event_id)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        iterator,
        media_type="text/event-stream",
        headers=headers,
    )
