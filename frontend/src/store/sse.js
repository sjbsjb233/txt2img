// SSE store: glue between ``api/sse.js`` and the rest of the app.
//
// Responsibilities:
//   - Own the single SSE connection for the lifetime of an authenticated
//     session. Auto-(re)connect on login, close on logout.
//   - Multiplex incoming events to subscribers keyed by event kind. The
//     design doc §8.5.1 enumerates the kinds; new ones added later
//     (admin SSE, etc.) need no changes here — subscribers just register
//     for whatever string the server emits.
//   - Surface the connection state to UI consumers (the App-level
//     ``ConnectionLost`` overlay subscribes to this).
//
// Why a hand-rolled subscription store and not a context provider? The
// App.jsx tree has at most one consumer of connection state and a
// handful of consumers of event kinds; the indirection of context +
// reducers would be more code with no benefit.
//
// We deliberately *don't* import ``api/client.js`` directly to avoid a
// circular dep with ``store/auth.js`` (which imports ``api/client.js``
// already). Instead we read the token through a lazy ``getToken``
// passed to the SSE client.

import { useEffect, useState } from "react";
import { createSSEClient } from "../api/sse.js";
import { getApiBase, getToken } from "../api/client.js";

// ---------------------------------------------------------------------
// State
// ---------------------------------------------------------------------

let client = null;
let connectionState = { status: "idle", attempts: 0 };

const eventListeners = new Map(); // kind -> Set<handler>
const stateListeners = new Set(); // Set<handler>

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function ssEUrl() {
  const base = getApiBase().replace(/\/+$/, "");
  return `${base}/api/sse`;
}

function notifyState() {
  for (const fn of stateListeners) {
    try {
      fn(connectionState);
    } catch {
      // One bad listener should not block others.
    }
  }
}

function dispatchEvent(kind, payload, id) {
  const handlers = eventListeners.get(kind);
  if (!handlers) return;
  for (const fn of handlers) {
    try {
      fn(payload, { kind, id });
    } catch {
      // Swallow handler errors — the SSE pipeline must keep flowing.
    }
  }
}

// ---------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------

/**
 * Open (or replace) the SSE connection. Idempotent: calling twice
 * without ``disconnect`` in between closes the previous client first
 * so we never have two streams open for the same tab.
 *
 * The caller (``App.jsx`` boot or ``LoginPage`` after a successful
 * login) is responsible for invoking this once a token is in
 * localStorage.
 */
export function connect() {
  if (client) {
    client.close();
    client = null;
  }
  client = createSSEClient({
    url: ssEUrl(),
    getToken,
    onEvent: dispatchEvent,
    onConnectionState: (next) => {
      connectionState = next;
      notifyState();
    },
  });
  client.start();
}

/**
 * Close the connection and forget the client. Used on logout / hard
 * 401 so the next login starts a fresh stream.
 */
export function disconnect() {
  if (client) {
    client.close();
    client = null;
  }
  connectionState = { status: "idle", attempts: 0 };
  notifyState();
}

/**
 * Force an immediate reconnect attempt regardless of backoff state.
 * Wired to the ``retry now`` button on the ConnectionLost overlay.
 */
export function reconnectNow() {
  if (client) {
    client.reconnectNow();
  } else {
    connect();
  }
}

// ---------------------------------------------------------------------
// Subscription API
// ---------------------------------------------------------------------

/**
 * Subscribe to one event kind. Returns an unsubscribe function. Multiple
 * components can subscribe to the same kind; all handlers fire in
 * registration order.
 *
 * @param {string}   kind     Event name as it appears on the wire.
 * @param {Function} handler  ``(payload, meta) => void``
 */
export function subscribe(kind, handler) {
  let bucket = eventListeners.get(kind);
  if (!bucket) {
    bucket = new Set();
    eventListeners.set(kind, bucket);
  }
  bucket.add(handler);
  return () => {
    const b = eventListeners.get(kind);
    if (!b) return;
    b.delete(handler);
    if (b.size === 0) eventListeners.delete(kind);
  };
}

/**
 * Subscribe to connection state changes. Returns unsubscribe.
 * Called immediately with the current state so subscribers don't
 * have to read it separately.
 */
export function subscribeConnectionState(handler) {
  stateListeners.add(handler);
  try {
    handler(connectionState);
  } catch {
    // ignore
  }
  return () => {
    stateListeners.delete(handler);
  };
}

/** Read the current connection state without subscribing. */
export function getConnectionState() {
  return connectionState;
}

// ---------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------

/**
 * Re-renders on every connection-state change. Use at the App level
 * (or anywhere a banner needs to react to ``'lost'``).
 */
export function useConnectionState() {
  const [state, setState] = useState(connectionState);
  useEffect(() => subscribeConnectionState(setState), []);
  return state;
}
