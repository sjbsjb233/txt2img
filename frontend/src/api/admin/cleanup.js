// Thin wrappers around /api/admin/cleanup/*.
//
// The endpoint is a single POST that does either a dry-run (response =
// summary) or kicks off an async task (response = {task_id, ...}).
// We expose two helper names so call sites read clearly even though
// they hit the same URL.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/cleanup";

/** GET /api/admin/cleanup/suggestions — fixed buckets with live counts. */
export function getSuggestions() {
  return apiFetch(`${BASE}/suggestions`);
}

/**
 * POST /api/admin/cleanup with dry_run=true. Returns
 * `{job_count, image_count, disk_bytes, disk_human}`.
 */
export function dryRunCleanup({ rules, exemptStarred = true }) {
  return apiFetch(BASE, {
    method: "POST",
    body: { rules, dry_run: true, exempt_starred: exemptStarred },
  });
}

/**
 * POST /api/admin/cleanup with dry_run=false. Returns the task state.
 */
export function executeCleanup({ rules, exemptStarred = true }) {
  return apiFetch(BASE, {
    method: "POST",
    body: { rules, dry_run: false, exempt_starred: exemptStarred },
  });
}

/** GET /api/admin/cleanup/<task_id> — poll task progress. */
export function getCleanupTask(taskId) {
  return apiFetch(`${BASE}/${encodeURIComponent(taskId)}`);
}
