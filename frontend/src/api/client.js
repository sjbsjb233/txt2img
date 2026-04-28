const DEFAULT_BASE = "http://127.0.0.1:8000";
const API_BASE_KEY = "api_base";
const TOKEN_KEY = "token";

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

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
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
    // Surface `message` as the human-friendly string and `code` as a stable
    // identifier callers can branch on for i18n / routing decisions.
    const detail = (data && typeof data === "object" && data.detail) || res.statusText || `HTTP ${res.status}`;
    let message;
    let code = null;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      message = typeof detail.message === "string" ? detail.message : JSON.stringify(detail);
      code = typeof detail.code === "string" ? detail.code : null;
    } else {
      message = String(detail);
    }
    const err = new Error(message);
    err.status = res.status;
    err.code = code;
    err.data = data;
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------------------
// Auth state helpers — single source of truth for the cached user identity.
// ---------------------------------------------------------------------------

const USER_KEY = "user";

export function getCurrentUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
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

export function clearAuth() {
  clearToken();
  setCurrentUser(null);
}
