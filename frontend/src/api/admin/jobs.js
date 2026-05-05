// Thin wrappers around /api/admin/jobs/*.
// Backend module: app/api/admin/jobs.py.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/jobs";

/**
 * GET /api/admin/jobs/{hash_id}/inspect
 * Returns the full Admin Job Inspector payload.
 */
export function inspectJob(hashId) {
  return apiFetch(`${BASE}/${encodeURIComponent(hashId)}/inspect`);
}

/** GET /api/admin/jobs/{hash_id}/upstream/{n} — raw redacted attempt log. */
export function getUpstreamLog(hashId, attemptNo) {
  return apiFetch(
    `${BASE}/${encodeURIComponent(hashId)}/upstream/${attemptNo}`,
  );
}

/** Build a URL pointing at the raw log endpoint (for new-tab links). */
export function rawLogUrl(hashId, attemptNo) {
  return `${BASE}/${encodeURIComponent(hashId)}/upstream/${attemptNo}`;
}

/** POST /api/admin/jobs/{hash_id}/requeue — clone params into a fresh QUEUED job. */
export function requeueJob(hashId) {
  return apiFetch(`${BASE}/${encodeURIComponent(hashId)}/requeue`, {
    method: "POST",
  });
}
