// Wrappers around GET /api/jobs/{hash}/derived (mask edit / outpaint
// derivation list — design doc backend §5.3).

import { apiFetch } from "./client.js";

export function getDerivedJobs(hashId, { limit = 50, cursor = null } = {}) {
  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  if (cursor) qs.set("cursor", cursor);
  return apiFetch(
    `/api/jobs/${encodeURIComponent(hashId)}/derived?${qs.toString()}`,
    { method: "GET" }
  );
}
