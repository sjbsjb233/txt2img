// Thin wrappers around /api/admin/providers/* + /api/admin/adapters.
// Mirrors the backend router in `app/api/admin/providers.py` (PR-06 + PR-16).
//
// Every request runs through apiFetch so non-admin tokens get the
// shared 403 → logout redirect for free.

import { apiFetch } from "../client.js";

const BASE = "/api/admin/providers";

/** GET /api/admin/providers — list all providers with live metrics. */
export function listProviders() {
  return apiFetch(BASE);
}

/** GET /api/admin/providers/<id>. */
export function getProvider(providerId) {
  return apiFetch(`${BASE}/${encodeURIComponent(providerId)}`);
}

/**
 * POST /api/admin/providers — create a provider.
 * @param {object} body matching the design doc §4.2 shape.
 */
export function createProvider(body) {
  return apiFetch(BASE, { method: "POST", body });
}

/** PATCH /api/admin/providers/<id>. */
export function patchProvider(providerId, body) {
  return apiFetch(`${BASE}/${encodeURIComponent(providerId)}`, {
    method: "PATCH",
    body,
  });
}

/** DELETE /api/admin/providers/<id>. */
export function deleteProvider(providerId) {
  return apiFetch(`${BASE}/${encodeURIComponent(providerId)}`, {
    method: "DELETE",
  });
}

/**
 * POST /api/admin/providers/<id>/topup
 * @param {number} amountCny positive number of CNY to credit.
 */
export function topupProvider(providerId, amountCny) {
  return apiFetch(`${BASE}/${encodeURIComponent(providerId)}/topup`, {
    method: "POST",
    body: { amount_cny: amountCny },
  });
}

/** POST /api/admin/providers/<id>/reset-circuit. */
export function resetCircuit(providerId) {
  return apiFetch(
    `${BASE}/${encodeURIComponent(providerId)}/reset-circuit`,
    { method: "POST" },
  );
}

/**
 * POST /api/admin/providers/<id>/test
 * Runs one upstream call without touching the ledger or metrics.
 * @param {{ model_id?: string, prompt?: string }} opts
 */
export function testProvider(providerId, opts = {}) {
  const body = {};
  if (opts.modelId) body.model_id = opts.modelId;
  if (opts.prompt) body.prompt = opts.prompt;
  return apiFetch(`${BASE}/${encodeURIComponent(providerId)}/test`, {
    method: "POST",
    body,
  });
}

/** PATCH /api/admin/providers/<id>/models/<model_id>. */
export function patchProviderModel(providerId, modelId, body) {
  return apiFetch(
    `${BASE}/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
    { method: "PATCH", body },
  );
}

/**
 * PATCH /api/admin/providers/<id>/tier-access
 * Replace-list semantics: passing `[]` removes every tier from access.
 */
export function patchTierAccess(providerId, tiers) {
  return apiFetch(
    `${BASE}/${encodeURIComponent(providerId)}/tier-access`,
    { method: "PATCH", body: { tiers } },
  );
}

/** GET /api/admin/adapters — read-only registry view. */
export function listAdapters() {
  return apiFetch("/api/admin/adapters");
}
