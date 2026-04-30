"""Admin-only SSE channel + connection diagnostics.

Two endpoints:

- ``GET /api/admin/sse`` — long-poll connection that yields the same
  SSE stream the user channel does, but populated with admin-only
  events (``provider_state``, ``provider_metrics``, ``worker_pool_state``,
  ``cleanup_progress``). The stream re-uses the existing :class:`SSEHub`
  keyed on the admin's user-id; broadcasters in
  ``app.domain.admin_broadcaster`` push to *every* admin's channel.
- ``GET /api/admin/sse/clients`` — list of currently-open SSE
  connections across the hub. Useful for "why isn't user X seeing
  task_created?" diagnostics.

Auth: same Bearer token flow as the user SSE route. Both endpoints
require admin and reject impersonate tokens.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.deps import CurrentAdmin
from app.domain.sse_hub import get_sse_hub

logger = logging.getLogger("txt2img.admin.sse")

router = APIRouter(prefix="/api/admin", tags=["admin", "sse"])


class SSEClientEntry(BaseModel):
    """One row in the admin SSE-clients diagnostics view."""

    user_id: str
    client_count: int


class SSEClientsResponse(BaseModel):
    """Diagnostics payload for ``GET /api/admin/sse/clients``."""

    total_clients: int
    users_with_clients: int
    max_per_user: int
    heartbeat_seconds: int
    buffered_events: int
    clients: list[SSEClientEntry]


@router.get("/sse")
async def admin_sse_stream(
    admin: CurrentAdmin,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Open one admin SSE channel for the authenticated admin.

    The hub treats admin tabs the same way as user tabs — events
    pushed via :func:`app.domain.admin_broadcaster` reach every active
    admin's id, and the per-user connection cap (``SSE_MAX_CONNECTIONS_PER_USER``)
    applies. Reuse keeps the wire protocol identical, so the frontend
    can multiplex through one ``EventSource``-like client.
    """
    hub = get_sse_hub()
    iterator = hub.stream(admin.id, last_event_id=last_event_id)

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


@router.get("/sse/clients", response_model=SSEClientsResponse)
async def admin_list_sse_clients(_admin: CurrentAdmin) -> SSEClientsResponse:
    """Return per-user connection counts for diagnostics.

    We deliberately don't expose individual ``client_id`` values — the
    operational question is "is user X currently connected?", not
    "which tab is which". The aggregate keeps the response small even
    for power users with many tabs.
    """
    hub = get_sse_hub()
    stats = hub.stats()

    # Reach into the hub's bookkeeping for the per-user breakdown. The
    # hub tracks clients by user-id; flattening into the response is a
    # one-liner. Done here rather than inside ``stats()`` so the public
    # diagnostic surface stays a stable shape.
    entries: list[SSEClientEntry] = []
    user_buckets = getattr(hub, "_clients", {})
    for user_id, clients in user_buckets.items():
        if not clients:
            continue
        entries.append(
            SSEClientEntry(user_id=user_id, client_count=len(clients))
        )
    entries.sort(key=lambda e: (-e.client_count, e.user_id))

    return SSEClientsResponse(
        total_clients=int(stats["total_clients"]),
        users_with_clients=int(stats["users_with_clients"]),
        max_per_user=int(stats["max_per_user"]),
        heartbeat_seconds=int(stats["heartbeat_seconds"]),
        buffered_events=int(stats["buffered_events"]),
        clients=entries,
    )
