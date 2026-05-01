// Thin wrapper around /api/admin/audit. Mirrors the backend router
// in `app/api/admin/audit.py` per design doc §13.10.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/audit";

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
 * GET /api/admin/audit — paginated audit log feed.
 *
 * @param {Object} opts
 * @param {string} [opts.actor]   user id or username
 * @param {string} [opts.action]  exact match or "user.*" prefix glob
 * @param {string} [opts.since]   ISO timestamp (inclusive)
 * @param {string} [opts.until]   ISO timestamp (exclusive)
 * @param {number} [opts.page=1]
 * @param {number} [opts.pageSize=50]
 */
export function listAudit({
  actor,
  action,
  since,
  until,
  page = 1,
  pageSize = 50,
} = {}) {
  return apiFetch(
    BASE +
      buildQuery({
        actor,
        action,
        since,
        until,
        page,
        page_size: pageSize,
      }),
  );
}
