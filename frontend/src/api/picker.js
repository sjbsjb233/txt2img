// Picker page API wrappers (PRD §9). Thin REST shims over apiFetch —
// the heavy lifting lives in the picker store.

import { apiFetch, getApiBase, getToken } from "./client.js";

// ---- Image-state writes ---------------------------------------------------

const STATE_TO_PATH = {
  picked: "pick",
  discarded: "discard",
  final: "final",
  deferred: "defer",
  unjudged: "unjudge",
};

export function writePickState(hashId, order, state) {
  const verb = STATE_TO_PATH[state];
  if (!verb) throw new Error(`unknown pick_state ${state}`);
  return apiFetch(
    `/api/jobs/${encodeURIComponent(hashId)}/images/${order}/${verb}`,
    { method: "POST" }
  );
}

// ---- Session-level operations --------------------------------------------

export function finalizeSession(sessionId) {
  return apiFetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/finalize`,
    { method: "POST" }
  );
}

export function unfinalizeSession(sessionId) {
  return apiFetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/unfinalize`,
    { method: "POST" }
  );
}

export function patchCursor(sessionId, cursorImageId) {
  return apiFetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/cursor`,
    { method: "PATCH", body: { cursor_image_id: cursorImageId } }
  );
}

export function getSessionPicker(sessionId) {
  return apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/picker`);
}

export function getPickerOverview() {
  return apiFetch("/api/picker/overview");
}

// ---- Vary seed ------------------------------------------------------------

export function varySeed({ sourceImageId, seed }) {
  return apiFetch("/api/jobs/vary", {
    method: "POST",
    body: { source_image_id: sourceImageId, seed: seed ?? null },
  });
}

// ---- Image fetch with auth (used by the queue) ---------------------------
//
// Returns a Promise<Blob | null>. 404 = "no longer on disk" → null.
// Any other error throws so the caller knows it's transient.

export async function fetchImageBlob(path) {
  const base = getApiBase().replace(/\/+$/, "");
  const token = getToken();
  const url = path.startsWith("http") ? path : `${base}${path}`;
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await fetch(url, { headers });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`fetch failed: ${res.status}`);
  }
  return res.blob();
}
