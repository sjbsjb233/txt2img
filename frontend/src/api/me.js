// Self-service account endpoints under /api/me/*. The backend lives
// in backend/app/api/me/ — this file is the thin frontend client used
// by the Settings page and the preferences store.

import { apiFetch } from "./client.js";

export const patchMe = (body) =>
  apiFetch("/api/me", { method: "PATCH", body });

export const getPreferences = () => apiFetch("/api/me/preferences");

export const patchPreferences = (body) =>
  apiFetch("/api/me/preferences", { method: "PATCH", body });

export const changePassword = (body) =>
  apiFetch("/api/me/password", { method: "POST", body });

export const listSessions = () => apiFetch("/api/me/sessions");

export const revokeSession = (id) =>
  apiFetch(`/api/me/sessions/${id}`, { method: "DELETE" });

export const revokeOtherSessions = () =>
  apiFetch("/api/me/sessions/revoke-others", { method: "POST" });

export const getDeletionRequest = () =>
  apiFetch("/api/me/deletion-request");

export const requestDeletion = (body) =>
  apiFetch("/api/me/deletion-request", { method: "POST", body });

export const withdrawDeletion = () =>
  apiFetch("/api/me/deletion-request", { method: "DELETE" });

export const getMe = () => apiFetch("/api/me");
