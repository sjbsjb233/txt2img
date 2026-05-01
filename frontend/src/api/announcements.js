// User-facing announcement endpoints (design doc §11.2).
//
// Three calls — list "active for me", mark one read, fetch a cover
// image. Admin fan-out and editing live under api/admin/announcements.

import { apiFetch, getApiBase } from "./client.js";

const BASE = "/api/announcements";

/** GET /api/announcements/active — returns only what I should currently see. */
export function getActiveAnnouncements() {
  return apiFetch(`${BASE}/active`);
}

/** POST /api/announcements/<id>/read — idempotent dismiss. */
export function markRead(id) {
  return apiFetch(`${BASE}/${encodeURIComponent(id)}/read`, { method: "POST" });
}

/** Build the cover image URL for an image-kind announcement. */
export function coverUrl(id) {
  return `${getApiBase().replace(/\/+$/, "")}${BASE}/${encodeURIComponent(id)}/cover`;
}
