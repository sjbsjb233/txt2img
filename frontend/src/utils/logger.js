// Frontend log shim that batches records and POSTs them to /api/client-logs.
//
// Design goals (matching docs/logging plan §5):
//   - One singleton; safe to import anywhere, including before mount.
//   - Records flush periodically (FLUSH_INTERVAL_MS) and on page unload
//     via navigator.sendBeacon so unhandled errors during navigation
//     still reach the backend.
//   - Never throw out of `log.*` — telemetry must not break the app it
//     is supposed to observe.
//   - Mirror to the developer console so local debugging still works.
//
// The backend route is /api/client-logs (see backend/app/api/client_logs.py).
// Records carry the route, build version, session id, user agent, and an
// optional `extra` bag. Stack traces (uncaught error / boundary) are
// passed through verbatim under `stack`.

import { getApiBase } from "../api/client.js";

const ENDPOINT_PATH = "/api/client-logs";
const FLUSH_INTERVAL_MS = 5000;
const MAX_BUFFER = 50;
const MAX_QUEUE = 200; // overall safety cap if backend is unreachable
const MAX_MSG_LEN = 1900;
const MAX_STACK_LEN = 7800;
const SESSION_ID_KEY = "client_log_session_id";

function _safeRandom() {
  try {
    if (
      typeof crypto !== "undefined" &&
      typeof crypto.randomUUID === "function"
    ) {
      return crypto.randomUUID().replace(/-/g, "");
    }
  } catch (_e) {
    /* ignore */
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function _sessionId() {
  try {
    let id = sessionStorage.getItem(SESSION_ID_KEY);
    if (!id) {
      id = _safeRandom();
      sessionStorage.setItem(SESSION_ID_KEY, id);
    }
    return id;
  } catch (_e) {
    return "ephemeral-" + _safeRandom();
  }
}

function _clip(value, maxLen) {
  if (value == null) return null;
  const s = typeof value === "string" ? value : String(value);
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen) + "...";
}

function _safeRoute() {
  try {
    return window.location.pathname + (window.location.search || "");
  } catch (_e) {
    return null;
  }
}

function _safeBuildVersion() {
  try {
    return (
      import.meta.env?.VITE_APP_VERSION ||
      import.meta.env?.VITE_BUILD_VERSION ||
      "dev"
    );
  } catch (_e) {
    return "dev";
  }
}

function _safeUserAgent() {
  try {
    return navigator.userAgent;
  } catch (_e) {
    return null;
  }
}

const buffer = [];
let flushTimer = null;
let flushing = false;
let disabled = false;

function _ensureTimer() {
  if (flushTimer != null) return;
  if (typeof window === "undefined") return;
  flushTimer = window.setInterval(() => {
    void flush();
  }, FLUSH_INTERVAL_MS);
}

function _consoleMirror(level, msg, extra) {
  if (typeof console === "undefined") return;
  // We deliberately downgrade error/warn to the matching console method
  // so the developer experience inside `dev` builds is unchanged.
  const fn =
    level === "error"
      ? console.error?.bind(console)
      : level === "warn"
        ? console.warn?.bind(console)
        : level === "debug"
          ? console.debug?.bind(console)
          : console.info?.bind(console);
  if (typeof fn === "function") {
    try {
      if (extra) fn(`[client] ${msg}`, extra);
      else fn(`[client] ${msg}`);
    } catch (_e) {
      /* ignore */
    }
  }
}

function _enqueue(level, msg, extra) {
  if (disabled) return;
  let stack = null;
  let extraOut = extra;
  if (extra && typeof extra === "object" && extra.stack) {
    stack = _clip(extra.stack, MAX_STACK_LEN);
    extraOut = { ...extra };
    delete extraOut.stack;
  }
  const item = {
    ts: new Date().toISOString(),
    level,
    msg: _clip(msg, MAX_MSG_LEN),
    route: _safeRoute(),
    build_version: _safeBuildVersion(),
    session_id: _sessionId(),
    user_agent: _safeUserAgent(),
    stack,
    extra: extraOut && typeof extraOut === "object" ? extraOut : undefined,
  };
  buffer.push(item);
  if (buffer.length > MAX_QUEUE) {
    // Drop oldest to keep memory bounded if /api/client-logs is down.
    buffer.splice(0, buffer.length - MAX_QUEUE);
  }
  _consoleMirror(level, msg, extra);
  _ensureTimer();
  if (buffer.length >= MAX_BUFFER) {
    // Best-effort immediate flush — fire-and-forget.
    void flush();
  }
}

function _endpoint() {
  try {
    const base = (getApiBase() || "").replace(/\/+$/, "");
    return `${base}${ENDPOINT_PATH}`;
  } catch (_e) {
    return ENDPOINT_PATH;
  }
}

function _beaconSend(payload, url) {
  if (typeof navigator === "undefined" || !navigator.sendBeacon) return false;
  try {
    const blob = new Blob([payload], {
      type: "application/json;charset=UTF-8",
    });
    return navigator.sendBeacon(url, blob);
  } catch (_e) {
    return false;
  }
}

async function flush({ useBeacon = false } = {}) {
  if (disabled || buffer.length === 0) return;
  // Beacon path bypasses the ``flushing`` guard: a regular interval
  // flush already in flight is keepalive-best-effort across navigation,
  // and anything newly buffered during its ``fetch`` would otherwise be
  // silently dropped on hide/pagehide/beforeunload. Snapshot+drain the
  // buffer and fire the beacon synchronously here.
  if (useBeacon) {
    const items = buffer.splice(0, buffer.length);
    const payload = JSON.stringify({ items });
    const url = _endpoint();
    const sent = _beaconSend(payload, url);
    if (!sent) {
      // Beacon refused (typically because the body is over the user
      // agent's per-beacon quota, default 64KB). Re-queue so the next
      // regular flush picks it up; we don't fall back to fetch here
      // because the page is mid-navigation and the request would just
      // get cancelled.
      buffer.unshift(...items);
      if (buffer.length > MAX_QUEUE) {
        buffer.splice(0, buffer.length - MAX_QUEUE);
      }
    }
    return;
  }
  if (flushing) return;
  flushing = true;
  const items = buffer.splice(0, buffer.length);
  const payload = JSON.stringify({ items });
  const url = _endpoint();
  let sent = false;
  try {
    try {
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
      });
      sent = true;
    } catch (_e) {
      sent = false;
    }
  } finally {
    flushing = false;
    if (!sent) {
      // Network is down — re-queue what we tried to send so the next
      // flush retries.  Bounded by MAX_QUEUE so we never grow without
      // limit.
      buffer.unshift(...items);
      if (buffer.length > MAX_QUEUE) {
        buffer.splice(0, buffer.length - MAX_QUEUE);
      }
    }
  }
}

function disable() {
  disabled = true;
  if (flushTimer != null) {
    try {
      window.clearInterval(flushTimer);
    } catch (_e) {
      /* ignore */
    }
    flushTimer = null;
  }
  buffer.length = 0;
}

function _wireUnload() {
  if (typeof window === "undefined") return;
  // ``visibilitychange → hidden`` is more reliable than ``beforeunload``
  // on mobile Safari; keep both hooks active so we fire at the last
  // possible moment.
  const onHidden = () => {
    if (document.visibilityState === "hidden") {
      void flush({ useBeacon: true });
    }
  };
  try {
    document.addEventListener("visibilitychange", onHidden);
    window.addEventListener("pagehide", () => flush({ useBeacon: true }));
    window.addEventListener("beforeunload", () => flush({ useBeacon: true }));
  } catch (_e) {
    /* ignore */
  }
}

let _wired = false;
function _ensureWired() {
  if (_wired) return;
  _wired = true;
  _wireUnload();
  _ensureTimer();
}

export const log = {
  debug(msg, extra) {
    _ensureWired();
    _enqueue("debug", msg, extra);
  },
  info(msg, extra) {
    _ensureWired();
    _enqueue("info", msg, extra);
  },
  warn(msg, extra) {
    _ensureWired();
    _enqueue("warn", msg, extra);
  },
  error(msg, extra) {
    _ensureWired();
    _enqueue("error", msg, extra);
  },
};

export function installGlobalHandlers() {
  if (typeof window === "undefined") return;
  _ensureWired();
  try {
    window.addEventListener("error", (ev) => {
      try {
        log.error("uncaught error", {
          msg: ev?.message,
          file: ev?.filename,
          line: ev?.lineno,
          col: ev?.colno,
          stack: ev?.error?.stack,
        });
      } catch (_e) {
        /* ignore */
      }
    });
    window.addEventListener("unhandledrejection", (ev) => {
      try {
        const reason = ev?.reason;
        log.error("unhandled promise rejection", {
          reason: reason ? String(reason).slice(0, 1000) : null,
          stack: reason?.stack,
        });
      } catch (_e) {
        /* ignore */
      }
    });
  } catch (_e) {
    /* ignore */
  }
}

// Exposed for tests + manual flush from devtools (`window.__txt2imgLogFlush()`).
export const __internals = { flush, disable, buffer };

if (typeof window !== "undefined") {
  try {
    window.__txt2imgLogFlush = () => flush();
  } catch (_e) {
    /* ignore */
  }
}

export default log;
