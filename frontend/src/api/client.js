import { isSilentErrorCode, messageForCode } from "../utils/errorCopy.js";

const DEFAULT_BASE = "http://127.0.0.1:8000";
const API_BASE_KEY = "api_base";
const TOKEN_KEY = "token";

// Codes that imply the user's session is no longer usable. When apiFetch
// sees one of these on an authenticated request it forces a logout and
// bounces the browser to /login. Kept as a Set so the membership check
// in the hot path is cheap.
const FORCE_LOGOUT_CODES = new Set(["ACCOUNT_DISABLED", "BLOCKED_BY_EMERGENCY"]);

// Hook that lets `api/auth` register itself for the force-logout path.
// We use late binding (instead of importing the store directly) to keep
// `api/client.js` free of circular dependencies — store/auth.js depends
// on this module, so this module can't depend on it.
let _onForceLogout = null;
export function registerForceLogoutHandler(fn) {
  _onForceLogout = fn;
}

export function getApiBase() {
  const stored = localStorage.getItem(API_BASE_KEY);
  if (stored) return stored;
  const fromEnv = import.meta.env?.VITE_API_BASE;
  if (fromEnv !== undefined) return fromEnv;
  return DEFAULT_BASE;
}

export function setApiBase(value) {
  if (value && value.trim()) {
    localStorage.setItem(API_BASE_KEY, value.trim());
  } else {
    localStorage.removeItem(API_BASE_KEY);
  }
}

// Token storage. Two layers:
//   - localStorage (default): persists across tabs and reloads. Used for
//     normal logins.
//   - sessionStorage (override): per-tab. Used by the impersonation
//     flow so the new window doesn't trample the admin tab's token.
//     When sessionStorage holds a token, it wins on this tab; the
//     admin tab still reads its localStorage admin token.
//
// All getters / setters go through these helpers so the override
// rule lives in exactly one place.

export function getToken() {
  const session = sessionStorage.getItem(TOKEN_KEY);
  if (session) return session;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  // Default writes go to localStorage so a normal login survives a
  // tab refresh / reopen. Use ``setSessionToken`` for the per-tab
  // impersonation flow.
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function setSessionToken(token) {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

export function clearToken() {
  // Clear both layers so any logout pathway (force-logout interceptor,
  // explicit logout, impersonation exit) releases everything.
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
}

export async function apiFetch(path, { method = "GET", body, auth = true, headers = {} } = {}) {
  const base = getApiBase().replace(/\/+$/, "");
  const url = `${base}${path.startsWith("/") ? path : `/${path}`}`;

  const finalHeaders = { Accept: "application/json", ...headers };
  let payload = body;
  if (body !== undefined && body !== null && !(body instanceof FormData)) {
    finalHeaders["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (auth) {
    const token = getToken();
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
  }

  let res;
  try {
    res = await fetch(url, { method, headers: finalHeaders, body: payload });
  } catch (e) {
    throw new Error(`network error: ${e.message || e}`);
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }

  if (!res.ok) {
    // Backend errors follow the §17 envelope `{detail: {code, message, field, extra}}`.
    // We surface `code` as the stable identifier callers branch on, and
    // override `message` with the frontend-controlled copy from
    // utils/errorCopy.js when one exists.
    const detail = (data && typeof data === "object" && data.detail) || res.statusText || `HTTP ${res.status}`;
    let backendMessage;
    let code = null;
    if (typeof detail === "string") {
      backendMessage = detail;
    } else if (detail && typeof detail === "object") {
      backendMessage =
        typeof detail.message === "string" ? detail.message : JSON.stringify(detail);
      code = typeof detail.code === "string" ? detail.code : null;
    } else {
      backendMessage = String(detail);
    }
    const mapped = messageForCode(code);
    const message =
      mapped !== undefined && mapped !== null ? mapped : backendMessage;

    const err = new Error(message);
    err.status = res.status;
    err.code = code;
    err.data = data;
    err.silent = isSilentErrorCode(code);

    // Auto-logout interceptor (FE-01 spec):
    //   - 401 on any authenticated request → token is bad
    //   - 403 with ACCOUNT_DISABLED or BLOCKED_BY_EMERGENCY → user kicked out
    //
    // We only redirect when the request was authenticated; the login
    // endpoint runs with auth=false and its own 401 means "wrong password",
    // not "session gone".
    if (auth) {
      const shouldKick =
        res.status === 401 ||
        (res.status === 403 && code && FORCE_LOGOUT_CODES.has(code));
      if (shouldKick) {
        if (typeof _onForceLogout === "function") {
          try { _onForceLogout(); } catch { /* ignore */ }
        } else {
          // Fallback for early boot: clear localStorage directly.
          clearAuth();
        }
        if (
          typeof window !== "undefined" &&
          window.location &&
          window.location.pathname !== "/login"
        ) {
          window.location.href = "/login";
        }
      }
    }
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------------------
// Auth state helpers — single source of truth for the cached user identity.
// ---------------------------------------------------------------------------

const USER_KEY = "user";

// Same per-tab override rule as the token: sessionStorage wins so the
// impersonation flow's user object overrides the admin's cached
// identity *only* on the impersonation tab.

export function getCurrentUser() {
  try {
    const raw =
      sessionStorage.getItem(USER_KEY) || localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const u = JSON.parse(raw);
    if (u && typeof u === "object" && typeof u.id === "string") return u;
    return null;
  } catch {
    return null;
  }
}

export function setCurrentUser(user) {
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  else localStorage.removeItem(USER_KEY);
}

export function setSessionUser(user) {
  if (user) sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  else sessionStorage.removeItem(USER_KEY);
}

export function clearAuth() {
  clearToken();
  setCurrentUser(null);
  setSessionUser(null);
}
