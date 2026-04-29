// Thin wrapper around GET /api/models (FE-03 / design doc §6.2).
//
// The Create page calls this on mount, and again on every
// `model_capabilities_changed` SSE event. Resp shape is documented in
// the backend `app/schemas/models.py`.

import { apiFetch } from "./client.js";

/**
 * Fetch the user's available models + sessions in a single call.
 * Returns `{ models: ModelDescriptor[], sessions: SessionEntry[] }`.
 */
export function getModels() {
  return apiFetch("/api/models");
}
