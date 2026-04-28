"""SSE hub: per-user multi-client fanout, replay buffer, and heartbeat.

This is the single in-process broadcaster the rest of the backend calls
when it wants to push something at the user's browser tabs. Two
responsibilities:

1. **Fanout.** Domain code calls :meth:`SSEHub.broadcast_to_user` with
   ``(user_id, kind, payload)``. The hub assigns a monotonic event id,
   records the event in a per-user replay buffer, and drops it into
   every live client's queue. Every browser tab the user has open then
   receives it through the streaming generator returned by
   :meth:`SSEHub.stream`.
2. **Reconnect resilience.** Every event also lives in a 5-minute
   in-memory ring keyed by user. When a client reconnects with a
   ``Last-Event-ID`` header, the hub replays everything strictly
   greater than that id before resuming live delivery — design doc
   §8.5.2 / §12.2.

Design constraints picked up from the doc that are easy to miss:

* **Single SSE channel per browser tab.** All event kinds (``hello``,
  ``heartbeat``, ``job_state``, ``job_progress``, ``task_created``,
  ``task_deleted``, ``announcement``, ``model_capabilities_changed``,
  ``connection_warning``) ride one connection. The frontend
  multiplexes in JS — see ``frontend/src/store/sse.js``.
* **Per-user connection cap.** A user with more than
  ``SSE_MAX_CONNECTIONS_PER_USER`` open tabs gets the *oldest*
  connection evicted (with a ``connection_warning`` farewell event)
  whenever a fresh one connects. We choose evict-oldest over reject-
  newest so the user's foreground tab — the one they just opened —
  is always the live one.
* **Heartbeats are per-client, not per-user.** Each ``stream``
  generator races its own ``asyncio.wait_for`` against the queue;
  on timeout it emits a heartbeat. A shared heartbeat timer would
  cause tabs that just opened to wait up to a full interval before
  their first ``: ping`` arrives.
* **Heartbeats do NOT enter the replay buffer.** They have no replay
  value — a client that reconnects already knows time has passed —
  and including them would crowd out real events from the bounded
  buffer.

The hub satisfies the :class:`~app.domain.job_lifecycle.BroadcastSink`
protocol so it can be installed as the lifecycle's sink at startup
without an adapter shim.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from app.config import get_settings

logger = logging.getLogger("txt2img.sse_hub")


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


def _format_event(*, event_id: str | None, kind: str, payload: Any) -> bytes:
    """Render one event into the SSE wire format.

    Spec: each event ends with a blank line. Field order is irrelevant
    so we emit them in (id, event, data) order for log readability.
    Multi-line ``data`` is rare for our payloads (we serialise compact
    JSON) but the spec says each ``\n`` in the data needs its own
    ``data:`` prefix; we handle it generically just in case.
    """
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {kind}")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for chunk in body.split("\n"):
        lines.append(f"data: {chunk}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _format_comment(text: str) -> bytes:
    """SSE comment line; survives every reverse proxy that buffers idle conns."""
    return f": {text}\n\n".encode("utf-8")


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


@dataclass
class _Event:
    """One broadcast unit pushed through a client queue.

    ``event_id`` is None for ephemeral events (heartbeat, hello,
    connection_warning) — they don't enter the replay buffer and
    therefore don't need an id the client could resume from.

    ``ts`` is monotonic-time seconds, used for the 5-minute buffer
    eviction. We deliberately use ``time.monotonic`` not wall-clock so
    the buffer doesn't misbehave if the system clock jumps.
    """

    event_id: str | None
    kind: str
    payload: Any
    ts: float


@dataclass
class _Client:
    """One connected browser tab.

    ``queue`` is bounded to keep memory usage finite if a slow client
    can't drain events as fast as the server is producing. When it
    fills up, we drop the client (and emit ``connection_warning``);
    that is preferable to backing pressure into the broadcast call site
    and stalling the executor.
    """

    client_id: str
    user_id: str
    queue: asyncio.Queue[_Event]
    connected_at: float = field(default_factory=time.monotonic)


# Sentinel so ``stream`` knows the hub asked it to terminate.
_TERMINATE = _Event(event_id=None, kind="__terminate__", payload=None, ts=0.0)


# ---------------------------------------------------------------------------
# SSEHub
# ---------------------------------------------------------------------------


# 5 minutes from design doc §8.5.2 / §12. Long enough to cover normal
# reverse-proxy hiccups, short enough that the in-memory ring stays small.
_BUFFER_SECONDS = 300

# Per-client queue depth. 256 events is roughly 4 minutes of high-traffic
# updates for a single user; in practice queues are near-empty because
# events drain as the network sends them.
_CLIENT_QUEUE_MAX = 256


class SSEHub:
    """Process-wide singleton that owns SSE state.

    Two pieces of shared state live behind ``self._lock``:

    * ``self._clients[user_id]`` — list of live clients, ordered by
      ``connected_at`` so eviction-by-oldest is just ``pop(0)``.
    * ``self._buffer[user_id]`` — deque of recent events for replay.

    Everything else is driven from outside: the FastAPI route grabs
    a ``stream`` generator and writes its bytes to the network; domain
    code calls ``broadcast_to_user``; the lifecycle calls the same
    method via the :class:`BroadcastSink` protocol.
    """

    def __init__(
        self,
        *,
        max_per_user: int | None = None,
        heartbeat_seconds: int | None = None,
        buffer_seconds: int = _BUFFER_SECONDS,
    ) -> None:
        settings = get_settings()
        self._max_per_user = (
            max_per_user
            if max_per_user is not None
            else settings.SSE_MAX_CONNECTIONS_PER_USER
        )
        self._heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else settings.SSE_HEARTBEAT_SECONDS
        )
        self._buffer_seconds = buffer_seconds

        self._clients: dict[str, list[_Client]] = {}
        self._buffer: dict[str, deque[_Event]] = {}
        # Monotonic counter shared across all users so event ids are
        # globally unique strings — clients only ever send back the id
        # they last saw, so collisions across users are impossible from
        # the client's point of view, but a global counter keeps logs
        # easy to grep.
        self._next_seq = 0
        # ``_seq_lock`` serialises the rare path that writes shared
        # state. Stream readers don't take the lock to consume from
        # their own queue — that's safe because each queue has exactly
        # one reader (the generator) and one writer set (broadcast +
        # eviction), and ``put_nowait`` / ``get`` on ``asyncio.Queue``
        # are themselves coroutine-safe.
        self._seq_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public: broadcast_to_user
    # ------------------------------------------------------------------

    async def broadcast_to_user(
        self, user_id: str, event: str, payload: Mapping[str, Any]
    ) -> None:
        """Append ``event`` to ``user_id``'s buffer and fan out to clients.

        Implements the :class:`~app.domain.job_lifecycle.BroadcastSink`
        protocol, so the hub plugs straight into the lifecycle sink.

        Failures here are best-effort and never raise: if a client's
        queue is full it gets dropped (with a synthesised
        ``connection_warning``) and the broadcast continues to the
        rest. The lifecycle has already committed the DB write by the
        time it calls us — losing one SSE delivery must not undo state.
        """
        ev = await self._make_event(event, dict(payload))
        await self._record_buffered(user_id, ev)
        await self._fanout(user_id, ev)

    async def _make_event(self, kind: str, payload: dict[str, Any]) -> _Event:
        async with self._seq_lock:
            self._next_seq += 1
            seq = self._next_seq
        # Encode wall-clock millis so admin tooling can grep an id back
        # to a rough time without consulting the buffer.
        event_id = f"{int(time.time() * 1000)}-{seq}"
        return _Event(
            event_id=event_id,
            kind=kind,
            payload=payload,
            ts=time.monotonic(),
        )

    async def _record_buffered(self, user_id: str, ev: _Event) -> None:
        async with self._seq_lock:
            buf = self._buffer.setdefault(user_id, deque())
            buf.append(ev)
            # Trim by age. ``deque`` doesn't have a O(1) range delete so
            # we popleft until the oldest is fresh enough; trimmed
            # events are guaranteed monotonically older than what
            # remains because we only ever append.
            cutoff = time.monotonic() - self._buffer_seconds
            while buf and buf[0].ts < cutoff:
                buf.popleft()

    async def _fanout(self, user_id: str, ev: _Event) -> None:
        # Snapshot under the lock; do the actual ``put`` outside so a
        # slow consumer can't stall an unrelated user's broadcast.
        async with self._seq_lock:
            clients = list(self._clients.get(user_id, ()))
        if not clients:
            return
        evicted: list[_Client] = []
        for client in clients:
            try:
                client.queue.put_nowait(ev)
            except asyncio.QueueFull:
                # The client has fallen far enough behind that we'd
                # rather drop it than memory-bomb the server. The
                # frontend will reconnect and replay from buffer.
                evicted.append(client)
        for client in evicted:
            await self._evict_client(client, reason="queue_full")

    # ------------------------------------------------------------------
    # Public: stream (used by the FastAPI route)
    # ------------------------------------------------------------------

    async def stream(
        self,
        user_id: str,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield SSE-encoded bytes for one client connection.

        The route handler hands this iterator to ``StreamingResponse``.
        Lifecycle of one connection:

        1. Acquire a slot, evicting the user's oldest connection if
           we're at the cap. The evicted client gets a
           ``connection_warning`` farewell event and the generator
           wraps up cleanly.
        2. Emit ``hello`` with server time + a fresh session id.
        3. Replay events from the buffer that the client missed
           (``Last-Event-ID``). If the header is missing or doesn't
           parse the client gets the full buffer; the frontend should
           dedupe by ``[event.id](http://event.id)`` so this is safe.
        4. Loop: read from this client's queue, emit; on
           ``asyncio.wait_for`` timeout, emit a heartbeat. Repeat
           until cancellation, terminator sentinel, or queue close.
        5. On exit (cancellation, network drop, terminator) clean up
           the slot.
        """
        client = _Client(
            client_id=uuid4().hex,
            user_id=user_id,
            queue=asyncio.Queue(maxsize=_CLIENT_QUEUE_MAX),
        )

        await self._register(client)

        try:
            yield _format_event(
                event_id=None,
                kind="hello",
                payload={
                    "server_time": _utc_now_iso(),
                    "session_id": client.client_id,
                    "heartbeat_seconds": self._heartbeat_seconds,
                },
            )

            # Replay anything the client missed. We do this before the
            # main loop so the client's reducer sees historical events
            # before any newly-arriving ones — same delivery order as
            # if the connection had been live throughout.
            for replayed in self._replay_for(user_id, last_event_id):
                yield _format_event(
                    event_id=replayed.event_id,
                    kind=replayed.kind,
                    payload=replayed.payload,
                )

            while True:
                try:
                    ev = await asyncio.wait_for(
                        client.queue.get(),
                        timeout=self._heartbeat_seconds,
                    )
                except asyncio.TimeoutError:
                    # Heartbeat: comment line + named event. The
                    # comment alone keeps even strict reverse proxies
                    # happy; the named event is what the frontend
                    # watches for to refresh its own watchdog.
                    yield _format_comment("ping")
                    yield _format_event(
                        event_id=None,
                        kind="heartbeat",
                        payload={"t": int(time.time() * 1000)},
                    )
                    continue
                if ev is _TERMINATE or ev.kind == "__terminate__":
                    # The hub asked us to wrap up. The eviction path
                    # has already enqueued a ``connection_warning``
                    # before this sentinel.
                    break
                yield _format_event(
                    event_id=ev.event_id,
                    kind=ev.kind,
                    payload=ev.payload,
                )
        except asyncio.CancelledError:
            # Client disconnected; let the cleanup happen in finally
            # then re-raise so Starlette tears down the response.
            raise
        finally:
            await self._unregister(client)

    # ------------------------------------------------------------------
    # Registration / eviction
    # ------------------------------------------------------------------

    async def _register(self, client: _Client) -> None:
        async with self._seq_lock:
            bucket = self._clients.setdefault(client.user_id, [])
            bucket.append(client)
            # Evict the oldest until we're at the cap. Strictly
            # greater than the cap because we just appended.
            to_evict: list[_Client] = []
            while len(bucket) > self._max_per_user:
                to_evict.append(bucket.pop(0))
        for old in to_evict:
            await self._send_warning(old, "evicted_oldest")
            # Push the terminator after the warning so the stream
            # generator drains the warning first.
            try:
                old.queue.put_nowait(_TERMINATE)
            except asyncio.QueueFull:
                # Already full — terminator gets lost, but the
                # generator's wait will hit a network error or the
                # queue will eventually drain; either way the slot
                # frees up.
                logger.debug(
                    "evict: terminator skipped (queue full) client=%s",
                    old.client_id,
                )

    async def _unregister(self, client: _Client) -> None:
        async with self._seq_lock:
            bucket = self._clients.get(client.user_id)
            if not bucket:
                return
            try:
                bucket.remove(client)
            except ValueError:
                pass
            if not bucket:
                self._clients.pop(client.user_id, None)

    async def _evict_client(self, client: _Client, *, reason: str) -> None:
        await self._send_warning(client, reason)
        try:
            client.queue.put_nowait(_TERMINATE)
        except asyncio.QueueFull:
            pass

    async def _send_warning(self, client: _Client, reason: str) -> None:
        """Best-effort delivery of ``connection_warning`` to a single client.

        Bypasses the buffer (one-shot, no replay value) and the per-
        user fanout (target is one specific tab). Drops silently if
        the client's queue is full — we're about to terminate it
        anyway.
        """
        warning = _Event(
            event_id=None,
            kind="connection_warning",
            payload={"reason": reason, "retry_after": 5},
            ts=time.monotonic(),
        )
        try:
            client.queue.put_nowait(warning)
        except asyncio.QueueFull:
            logger.debug(
                "warning: queue full for client=%s; dropping",
                client.client_id,
            )

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def _replay_for(
        self, user_id: str, last_event_id: str | None
    ) -> list[_Event]:
        """Return buffered events strictly newer than ``last_event_id``.

        ``last_event_id`` is the value the client last saw, in our
        ``"<wallclock_ms>-<seq>"`` format. We compare on the trailing
        ``seq`` because it's the only piece guaranteed to be
        monotonic; the wallclock prefix is only there for log
        readability and could go backwards if the system clock is
        adjusted mid-flight.

        If parsing fails we replay everything in the buffer — the
        frontend dedupes by ``[event.id](http://event.id)`` so a few extra
        events delivered after a sketchy reconnect are harmless.
        """
        buf = self._buffer.get(user_id)
        if not buf:
            return []
        threshold = self._parse_seq(last_event_id)
        if threshold is None:
            return list(buf)
        out: list[_Event] = []
        for ev in buf:
            seq = self._parse_seq(ev.event_id)
            if seq is None:
                # Buffered events always have ids, but be defensive.
                continue
            if seq > threshold:
                out.append(ev)
        return out

    @staticmethod
    def _parse_seq(event_id: str | None) -> int | None:
        if not event_id:
            return None
        # Format ``<wallclock_ms>-<seq>``. The seq is what we care about
        # for ordering. Be lenient — return ``None`` rather than raising
        # on a malformed header from the client.
        parts = event_id.rsplit("-", 1)
        try:
            return int(parts[-1])
        except (ValueError, IndexError):
            return None

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Snapshot used by tests + future admin SSE diagnostics route."""
        return {
            "users_with_clients": len(self._clients),
            "total_clients": sum(len(v) for v in self._clients.values()),
            "buffered_events": sum(len(v) for v in self._buffer.values()),
            "max_per_user": self._max_per_user,
            "heartbeat_seconds": self._heartbeat_seconds,
        }

    def client_count_for_user(self, user_id: str) -> int:
        return len(self._clients.get(user_id, ()))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: SSEHub | None = None
_instance_lock = threading.Lock()


def get_sse_hub() -> SSEHub:
    """Return the process-wide :class:`SSEHub` singleton, creating it lazily."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = SSEHub()
    return _instance


def reset_sse_hub_for_tests() -> None:
    """Drop the singleton so each test gets a fresh hub. Tests-only."""
    global _instance
    with _instance_lock:
        _instance = None


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
