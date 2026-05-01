// Thin wrappers around /api/admin/metrics/*. Mirrors the backend
// router in `app/api/admin/metrics.py` per design doc §13.9.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/metrics";

function buildQuery(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}

/** GET /api/admin/metrics/overview — single dashboard snapshot. */
export function getOverview() {
  return apiFetch(`${BASE}/overview`);
}

/**
 * GET /api/admin/metrics/timeseries — bucketed line-chart data.
 *
 * @param {Object} opts
 * @param {"jobs_count"|"success_rate"|"p50_latency"|"provider_balance"} opts.metric
 * @param {"24h"|"7d"|"30d"} [opts.range="24h"]
 * @param {"1m"|"5m"|"1h"|"1d"} [opts.bucket]
 * @param {string} [opts.providerId]
 * @param {string} [opts.model]
 */
export function getTimeseries({ metric, range = "24h", bucket, providerId, model }) {
  // Sensible default bucket per range so the caller doesn't have to
  // remember the cap math (24h/1m would 422; 30d/1m too).
  const defaultBucket = range === "30d" ? "1h" : range === "7d" ? "1h" : "5m";
  return apiFetch(
    `${BASE}/timeseries` +
      buildQuery({
        metric,
        range,
        bucket: bucket || defaultBucket,
        provider_id: providerId,
        model,
      }),
  );
}
