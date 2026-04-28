// Auth store — a small subscription-based singleton. Exists so React
// components can react to login / logout / impersonation without each
// one rolling its own `useState + setInterval(localStorage)` polling
// loop. We deliberately don't pull in zustand / redux for one piece of
// state.
//
// The single source of truth for *cached identity* is still localStorage
// (managed via `api/client.js` helpers); this module just wraps reads
// with subscription glue and provides high-level operations like
// `logout()` which call the backend before clearing local state.

import { useEffect, useState } from "react";
import * as authApi from "../api/auth.js";
import {
  clearAuth,
  getCurrentUser,
  getToken,
  registerForceLogoutHandler,
  setCurrentUser,
  setToken,
} from "../api/client.js";

const listeners = new Set();

function notify() {
  for (const fn of listeners) {
    try { fn(); } catch { /* one bad listener shouldn't break the others */ }
  }
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function getUser() {
  return getCurrentUser();
}

export function isAuthenticated() {
  return Boolean(getToken()) && Boolean(getCurrentUser());
}

export function isAdmin() {
  return getCurrentUser()?.role === "admin";
}

// ---------------------------------------------------------------------------
// Writes
// ---------------------------------------------------------------------------

/**
 * Persist a successful login: token + user in localStorage, then notify
 * subscribers. The login page calls this immediately after `authApi.login`
 * resolves.
 */
export function setSession({ accessToken, user }) {
  setToken(accessToken);
  setCurrentUser(user);
  notify();
}

/**
 * Soft logout: best-effort POST /api/auth/logout, then clear local
 * state, then notify subscribers. Failures on the network call are
 * swallowed because the backend is sessionless — clearing the client is
 * what actually logs the user out.
 */
export async function logout() {
  try {
    await authApi.logout();
  } catch { /* ignore */ }
  clearAuth();
  notify();
}

/**
 * Hard logout used by the apiFetch 401/403 interceptor. Skips the
 * network call (it would just 401 again), clears local state, notifies.
 * Caller is responsible for redirecting to /login.
 */
export function forceLogout() {
  clearAuth();
  notify();
}

// Wire ourselves into apiFetch's force-logout interceptor (401 / 403
// ACCOUNT_DISABLED / 403 BLOCKED_BY_EMERGENCY → clear local state). This
// runs once when the module is first imported, which is before any
// authenticated request thanks to the App → RequireAuth → store import
// chain.
registerForceLogoutHandler(forceLogout);

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

/**
 * Re-renders the calling component whenever auth state changes. Returns
 * a snapshot of `{user, isAuthenticated, isAdmin}`.
 */
export function useAuth() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return {
    user: getUser(),
    isAuthenticated: isAuthenticated(),
    isAdmin: isAdmin(),
  };
}
