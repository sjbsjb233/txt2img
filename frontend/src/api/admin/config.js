// Thin wrappers around /api/admin/config/*.
// PATCH accepts a flat key→value object (design doc §13.5) so weight
// changes can be sent atomically — the backend rejects any update
// that breaks the "weights sum to 1" invariant.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/config";

/** GET /api/admin/config — full snapshot. */
export function getConfig() {
  return apiFetch(BASE);
}

/** GET /api/admin/config/<key> — single key. */
export function getConfigKey(key) {
  return apiFetch(`${BASE}/${encodeURI(key)}`);
}

/** PATCH /api/admin/config — multi-key update; returns the accepted values. */
export function patchConfig(updates) {
  return apiFetch(BASE, { method: "PATCH", body: updates });
}
