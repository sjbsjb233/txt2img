// Thin wrappers around /api/admin/tiers/*.
// Tiers are fixed (vip / premium / standard / free) — no create/delete.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/tiers";

/** GET /api/admin/tiers — full tier list. */
export function listTiers() {
  return apiFetch(BASE);
}

/**
 * PATCH /api/admin/tiers/<tier> — partial update one tier row.
 * Hot-reloaded into the in-memory TierConfig on success.
 */
export function patchTier(tier, body) {
  return apiFetch(`${BASE}/${encodeURIComponent(tier)}`, {
    method: "PATCH",
    body,
  });
}
