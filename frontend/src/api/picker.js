// Picker page API wrappers (PRD v1).
//
// Three operation classes:
//   1. Per-image judgment writes (pick / discard / final / defer / unjudge)
//   2. Session-level reads + writes (picker bundle, cursor, finalize)
//   3. Deck overview (entry-page dashboard)
//
// All endpoints share the apiFetch wrapper so 401 / 403 interception
// follows the same path as the rest of the app.

import { apiFetch, getApiBase, getToken } from "./client.js";

// ---------------------------------------------------------------------
// Per-image judgment
// ---------------------------------------------------------------------

function _pickEndpoint(hashId, order, verb) {
  return `/api/jobs/${encodeURIComponent(hashId)}/images/${encodeURIComponent(
    order
  )}/${verb}`;
}

export function pickImage(hashId, order) {
  return apiFetch(_pickEndpoint(hashId, order, "pick"), { method: "POST" });
}

export function discardImage(hashId, order) {
  return apiFetch(_pickEndpoint(hashId, order, "discard"), { method: "POST" });
}

export function finalImage(hashId, order) {
  return apiFetch(_pickEndpoint(hashId, order, "final"), { method: "POST" });
}

export function deferImage(hashId, order) {
  return apiFetch(_pickEndpoint(hashId, order, "defer"), { method: "POST" });
}

export function unjudgeImage(hashId, order) {
  return apiFetch(_pickEndpoint(hashId, order, "unjudge"), { method: "POST" });
}

// ---------------------------------------------------------------------
// Session-level
// ---------------------------------------------------------------------

export function getSessionPicker(sessionId) {
  return apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/picker`, {
    method: "GET",
  });
}

export function patchSessionCursor(sessionId, cursorImageId) {
  return apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/cursor`, {
    method: "PATCH",
    body: { cursor_image_id: cursorImageId },
  });
}

export function finalizeSession(sessionId) {
  return apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/finalize`, {
    method: "POST",
  });
}

export function unfinalizeSession(sessionId) {
  return apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/unfinalize`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------
// Deck overview
// ---------------------------------------------------------------------

export function getPickerOverview() {
  return apiFetch("/api/picker/overview", { method: "GET" });
}

// ---------------------------------------------------------------------
// Image fetch (mirrors archive — used by AuthorizedImage)
// ---------------------------------------------------------------------

function joinBase(path) {
  const base = getApiBase().replace(/\/+$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function pickerImageThumbUrl(hashId, order) {
  return joinBase(
    `/api/jobs/${encodeURIComponent(hashId)}/images/${encodeURIComponent(order)}/thumb`
  );
}

export function pickerImageOriginalUrl(hashId, order) {
  return joinBase(
    `/api/jobs/${encodeURIComponent(hashId)}/images/${encodeURIComponent(order)}/original`
  );
}
