// SSE client for the single ``GET /api/sse`` long connection.
//
// This module is the only place in the frontend that talks to
// ``text/event-stream``. It is built on ``fetch`` + the streaming Body
// reader rather than the native ``EventSource`` because:
//
//   1. ``EventSource`` cannot send custom headers, so it can't carry our
//      ``Authorization: Bearer <token>`` (the backend's auth boundary).
//   2. ``EventSource``'s reconnect path is a black box: you can't cap the
//      retry rate, you can't tell it to give up after N failures, and
//      you can't override its ``Last-Event-ID`` header injection.
//
// What this client does, in priority order:
//
//   - Holds one open stream at a time. Caller calls ``connect()`` to
//     start it, ``close()`` to stop it forever.
//   - Parses the SSE wire format incrementally as bytes arrive.
//   - Tracks the most recent ``id`` seen and re-sends it as the
//     ``Last-Event-ID`` header on every reconnect, so the hub can
//     replay anything we missed.
//   - Reconnects with exponential backoff capped at 30s; reports a
//     coarse ``status`` (``'connecting'`` / ``'open'`` /
//     ``'reconnecting'`` / ``'lost'`` / ``'unauthorized'``) to the
//     caller via the ``onConnectionState`` callback.
//   - Watches a heartbeat timer: if no traffic arrives for 2× the
//     server's heartbeat interval, abort the underlying fetch so the
//     reconnect loop kicks in. This catches the "TCP looks alive but
//     the proxy ate the stream" failure mode that ``EventSource`` is
//     also bad at.
//   - Stops trying on auth failure (401 / 403 ACCOUNT_DISABLED /
//     BLOCKED_BY_EMERGENCY) so the caller can route the user out
//     instead of looping forever.

// ---------------------------------------------------------------------
// Tunables
// ---------------------------------------------------------------------

const RETRY_DELAYS_MS = [0, 1000, 2000, 4000, 8000, 16000, 30000];
// Connection-state status reported to ``onConnectionState``. Keep in
// sync with frontend/src/store/sse.js — it branches on these strings.
//
//   connecting     — first attempt, no event seen yet
//   open           — at least one byte (or hello frame) received
//   reconnecting   — retrying after a recoverable failure (1..4 attempts)
//   lost           — 5+ failures in a row; show ConnectionLost overlay
//   unauthorized   — auth failed; do not retry, caller logs out

// Default heartbeat interval (s). The server sends one in the ``hello``
// frame; until we receive it we use this as a watchdog. Matches the
// backend default in app/config.py SSE_HEARTBEAT_SECONDS.
const DEFAULT_HEARTBEAT_SECONDS = 15;

// Multiplier on the heartbeat interval for the watchdog timeout. If we
// see no bytes for this many heartbeat windows, we abort and reconnect.
const HEARTBEAT_WATCHDOG_FACTOR = 2;

// Window the watchdog wakes up on to check the last-seen timestamp.
// Independent of heartbeat interval so we can adapt mid-flight without
// resetting the timer.
const WATCHDOG_TICK_MS = 2000;

// ---------------------------------------------------------------------
// Public factory
// ---------------------------------------------------------------------

/**
 * Build a stoppable SSE client.
 *
 * @param {object}   opts
 * @param {string}   opts.url                 Full URL to ``GET /api/sse``.
 * @param {() => string|null} opts.getToken   Returns the current bearer token.
 * @param {(kind:string, payload:any, id:string|null) => void} opts.onEvent
 *        Called for every event other than ``hello``/``heartbeat``/
 *        ``connection_warning`` (which are handled internally).
 * @param {(state: {status:string, attempts:number, reason?:string}) => void} opts.onConnectionState
 *        Called whenever the connection status changes.
 *
 * @returns {{ start: () => void, close: () => void, reconnectNow: () => void }}
 */
export function createSSEClient({
  url,
  getToken,
  onEvent,
  onConnectionState,
}) {
  let attempts = 0;
  let lastEventId = null;
  let abortCtrl = null;
  let retryTimer = null;
  let watchdogTimer = null;
  let lastSeenAt = 0;
  let stopped = false;
  let started = false;
  let heartbeatSeconds = DEFAULT_HEARTBEAT_SECONDS;

  function emitState(status, extra = {}) {
    if (typeof onConnectionState === "function") {
      try {
        onConnectionState({ status, attempts, ...extra });
      } catch {
        // A bad listener must not break the loop.
      }
    }
  }

  function clearRetryTimer() {
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
  }

  function clearWatchdog() {
    if (watchdogTimer !== null) {
      clearInterval(watchdogTimer);
      watchdogTimer = null;
    }
  }

  function scheduleRetry() {
    clearRetryTimer();
    if (stopped) return;
    attempts += 1;
    const delay =
      RETRY_DELAYS_MS[Math.min(attempts, RETRY_DELAYS_MS.length - 1)];
    if (attempts >= 5) {
      emitState("lost");
    } else {
      emitState("reconnecting");
    }
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void connect();
    }, delay);
  }

  function startWatchdog() {
    clearWatchdog();
    lastSeenAt = Date.now();
    watchdogTimer = setInterval(() => {
      const idleMs = Date.now() - lastSeenAt;
      if (idleMs > heartbeatSeconds * 1000 * HEARTBEAT_WATCHDOG_FACTOR) {
        // Server should have heartbeated by now. Abort the fetch and
        // let the reconnect path take over. The retry has its own
        // backoff so we won't busy-loop here.
        if (abortCtrl) {
          try {
            abortCtrl.abort();
          } catch {
            // Already aborted.
          }
        }
      }
    }, WATCHDOG_TICK_MS);
  }

  function bumpLastSeen() {
    lastSeenAt = Date.now();
  }

  // ------------------------------------------------------------------
  // The connect / read / parse loop
  // ------------------------------------------------------------------

  async function connect() {
    if (stopped) return;
    const token = typeof getToken === "function" ? getToken() : null;
    if (!token) {
      // No token yet. Pause; the caller is expected to call ``reconnectNow``
      // after login lands.
      emitState("connecting");
      return;
    }

    const isFirstAttempt = attempts === 0;
    emitState(isFirstAttempt ? "connecting" : "reconnecting");

    abortCtrl = new AbortController();
    const headers = {
      Accept: "text/event-stream",
      Authorization: `Bearer ${token}`,
      "Cache-Control": "no-cache",
    };
    if (lastEventId) {
      headers["Last-Event-ID"] = lastEventId;
    }

    let res;
    try {
      res = await fetch(url, {
        method: "GET",
        headers,
        signal: abortCtrl.signal,
        // Important: don't send cookies — we use bearer auth only and
        // some browsers (Safari) over-eagerly set credentials on EventSource.
        credentials: "omit",
      });
    } catch (e) {
      // AbortError happens during normal shutdown / watchdog kicks; it
      // is not an error condition for the user.
      if (e?.name === "AbortError") return;
      scheduleRetry();
      return;
    }

    // Auth failures are terminal. The caller's force-logout interceptor
    // (registered in store/auth.js) handles the user-visible side; we
    // just stop trying.
    if (res.status === 401 || res.status === 403) {
      stopped = true;
      emitState("unauthorized", { reason: `http_${res.status}` });
      return;
    }
    if (!res.ok || !res.body) {
      scheduleRetry();
      return;
    }

    attempts = 0;
    emitState("open");
    startWatchdog();

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    try {
      while (!stopped) {
        const { done, value } = await reader.read();
        if (done) break;
        bumpLastSeen();
        buffer += decoder.decode(value, { stream: true });

        // SSE event boundary is a blank line. Tolerate ``\r\n\r\n`` for
        // proxies that rewrite line endings.
        let boundary;
        while ((boundary = findBoundary(buffer)) !== -1) {
          const block = buffer.slice(0, boundary.start);
          buffer = buffer.slice(boundary.end);
          handleBlock(block);
        }
      }
    } catch (e) {
      if (e?.name !== "AbortError") {
        // Any other error (network drop, body decode failure) → reconnect.
      }
    } finally {
      clearWatchdog();
    }

    if (!stopped) {
      scheduleRetry();
    }
  }

  function handleBlock(block) {
    if (!block) return;
    let id = null;
    let event = "message";
    const dataLines = [];

    for (const rawLine of block.split("\n")) {
      const line = rawLine.replace(/\r$/, "");
      if (line === "") continue;
      if (line.startsWith(":")) continue; // SSE comment line
      const colonIdx = line.indexOf(":");
      if (colonIdx === -1) continue;
      const field = line.slice(0, colonIdx);
      // Spec says one optional space after the colon should be consumed.
      const value = line.slice(colonIdx + 1).replace(/^ /, "");
      if (field === "id") id = value;
      else if (field === "event") event = value;
      else if (field === "data") dataLines.push(value);
      // ``retry`` is intentionally ignored — we manage backoff ourselves.
    }

    if (id) lastEventId = id;
    let payload = null;
    if (dataLines.length > 0) {
      const raw = dataLines.join("\n");
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = raw;
      }
    }

    // Internal events that don't bubble up to the caller.
    if (event === "hello") {
      if (
        payload &&
        typeof payload === "object" &&
        Number.isFinite(payload.heartbeat_seconds)
      ) {
        heartbeatSeconds = payload.heartbeat_seconds;
      }
      return;
    }
    if (event === "heartbeat") {
      // Watchdog already re-armed via ``bumpLastSeen``; nothing further.
      return;
    }
    if (event === "connection_warning") {
      // Server is asking us to reconnect / accept eviction. Treat as a
      // soft reconnect after the response stream closes; we don't need
      // to surface it to the app layer for v1.
      return;
    }

    if (typeof onEvent === "function") {
      try {
        onEvent(event, payload, id);
      } catch {
        // Listener errors must not corrupt the stream.
      }
    }
  }

  // ------------------------------------------------------------------
  // Imperative API
  // ------------------------------------------------------------------

  function start() {
    if (started || stopped) return;
    started = true;
    void connect();
  }

  function close() {
    stopped = true;
    started = false;
    clearRetryTimer();
    clearWatchdog();
    if (abortCtrl) {
      try {
        abortCtrl.abort();
      } catch {
        // Already aborted.
      }
      abortCtrl = null;
    }
  }

  function reconnectNow() {
    if (stopped) return;
    clearRetryTimer();
    if (abortCtrl) {
      try {
        abortCtrl.abort();
      } catch {
        // Already aborted.
      }
    }
    attempts = 0;
    emitState("connecting");
    void connect();
  }

  return { start, close, reconnectNow };
}

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

/**
 * Find the next event boundary in ``buf``. Returns ``-1`` if none.
 *
 * Handles both ``\n\n`` (the spec) and ``\r\n\r\n`` (some proxies). The
 * caller slices ``[0..start]`` as the event block and ``buf[end..]`` as
 * the remainder.
 */
function findBoundary(buf) {
  const lf = buf.indexOf("\n\n");
  const crlf = buf.indexOf("\r\n\r\n");
  if (lf === -1 && crlf === -1) return -1;
  if (crlf !== -1 && (lf === -1 || crlf < lf)) {
    return { start: crlf, end: crlf + 4 };
  }
  return { start: lf, end: lf + 2 };
}
