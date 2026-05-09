// Wrappers around the v0.3 batch endpoints (frontend/backend doc v0.3).
//
// All routes are JSON; the heavy multipart upload still lives in
// ``api/jobs.js`` and the batch flow merely passes ``batch_id`` through
// the existing job route.

import { apiFetch } from "./client.js";

/**
 * GET /api/batches
 * @param {{ status?: string, cursor?: string|null, limit?: number }} opts
 */
export function listBatches(opts = {}) {
  const qs = [];
  if (opts.status) qs.push(`status=${encodeURIComponent(opts.status)}`);
  if (opts.cursor) qs.push(`cursor=${encodeURIComponent(opts.cursor)}`);
  if (opts.limit) qs.push(`limit=${opts.limit}`);
  const tail = qs.length ? `?${qs.join("&")}` : "";
  return apiFetch(`/api/batches${tail}`);
}

/** GET /api/batches/<id> */
export function getBatch(id) {
  return apiFetch(`/api/batches/${encodeURIComponent(id)}`);
}

/**
 * POST /api/batches
 * @param {{ title: string, total_job_count: number, spec: object }} body
 */
export function createBatch(body) {
  return apiFetch("/api/batches", { method: "POST", body });
}

/** POST /api/batches/<id>/finalize_submission */
export function finalizeBatch(id) {
  return apiFetch(
    `/api/batches/${encodeURIComponent(id)}/finalize_submission`,
    { method: "POST" }
  );
}

/** POST /api/batches/<id>/cancel */
export function cancelBatchQueued(id) {
  return apiFetch(`/api/batches/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
}

/** DELETE /api/batches/<id>?keep_jobs=true */
export function deleteBatch(id, { keepJobs = true } = {}) {
  const qs = `?keep_jobs=${keepJobs ? "true" : "false"}`;
  return apiFetch(`/api/batches/${encodeURIComponent(id)}${qs}`, {
    method: "DELETE",
  });
}
