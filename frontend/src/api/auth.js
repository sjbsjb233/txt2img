// Thin wrappers around the four auth endpoints. Centralized so callers
// don't have to remember which paths require auth, what the body shape
// is, or how the captcha flow reads its site_key. Page components import
// from here, not from `api/client.js` directly.

import { apiFetch } from "./client.js";

/**
 * POST /api/auth/captcha-check
 * Returns `{captcha_required, captcha_provider, site_key, reason}`.
 */
export function captchaCheck(username) {
  return apiFetch("/api/auth/captcha-check", {
    method: "POST",
    body: { username },
    auth: false,
  });
}

/**
 * POST /api/auth/login
 * Returns `{access_token, token_type, user}` on success.
 * Throws an Error with `.code` set to one of the §17 error codes on failure.
 */
export function login({ username, password, captchaToken }) {
  return apiFetch("/api/auth/login", {
    method: "POST",
    body: { username, password, captcha_token: captchaToken ?? null },
    auth: false,
  });
}

/**
 * POST /api/auth/logout — best-effort. The backend is sessionless so the
 * real logout work is on the client (clearing localStorage + redirecting).
 * Failures are swallowed by the caller.
 */
export function logout() {
  return apiFetch("/api/auth/logout", { method: "POST" });
}

/**
 * GET /api/me — identity probe. Used by guards / debug, not by the login
 * happy path (login already returns the user object).
 */
export function me() {
  return apiFetch("/api/me");
}
