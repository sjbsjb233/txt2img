// Thin wrappers around /api/admin/users/*. Mirrors the backend router
// in `app/api/admin/users.py` 1:1 so call sites can read like the
// design doc §13.2 surface.
//
// All requests are authenticated; the backend rejects non-admins with a
// 403 FORBIDDEN. The shared apiFetch interceptor handles the redirect.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/users";

function buildQuery(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}

/**
 * GET /api/admin/users — list with filter, sort, pagination.
 * @returns {Promise<{items: object[], page: number, page_size: number, total: number}>}
 */
export function listUsers({ q, tier, status, sort, page, pageSize } = {}) {
  return apiFetch(
    BASE +
      buildQuery({
        q,
        tier,
        status,
        sort,
        page,
        page_size: pageSize,
      }),
  );
}

/** GET /api/admin/users/<id> — full detail with 30-day rollups. */
export function getUser(userId) {
  return apiFetch(`${BASE}/${encodeURIComponent(userId)}`);
}

/** POST /api/admin/users — create a new user. Returns the detail view. */
export function createUser(body) {
  return apiFetch(BASE, { method: "POST", body });
}

/** PATCH /api/admin/users/<id> — partial update. */
export function patchUser(userId, body) {
  return apiFetch(`${BASE}/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body,
  });
}

/** DELETE /api/admin/users/<id> — soft delete (frees the username). */
export function deleteUser(userId) {
  return apiFetch(`${BASE}/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}

export function disableUser(userId) {
  return apiFetch(`${BASE}/${encodeURIComponent(userId)}/disable`, {
    method: "POST",
  });
}

export function enableUser(userId) {
  return apiFetch(`${BASE}/${encodeURIComponent(userId)}/enable`, {
    method: "POST",
  });
}

export function resetPassword(userId, newPassword) {
  return apiFetch(`${BASE}/${encodeURIComponent(userId)}/reset-password`, {
    method: "POST",
    body: { new_password: newPassword },
  });
}

/**
 * POST /api/admin/users/<id>/impersonate
 * Returns `{access_token, expires_in_seconds, target_user_id, target_username}`.
 * The caller is responsible for opening a new window with the token in
 * the URL hash (so the existing admin tab keeps its admin session).
 */
export function impersonate(userId) {
  return apiFetch(`${BASE}/${encodeURIComponent(userId)}/impersonate`, {
    method: "POST",
  });
}

/** POST /api/admin/users/bulk — bulk patch. Whitelisted fields only. */
export function bulkPatch(ids, patch) {
  return apiFetch(`${BASE}/bulk`, {
    method: "POST",
    body: { ids, patch },
  });
}

/** GET /api/admin/users/<id>/jobs — admin view of one user's jobs. */
export function listUserJobs(userId, { status, page, pageSize } = {}) {
  return apiFetch(
    `${BASE}/${encodeURIComponent(userId)}/jobs` +
      buildQuery({ status, page, page_size: pageSize }),
  );
}
