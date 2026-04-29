// Session CRUD wrappers (design doc §6.6 / §16.4).
//
// Used by the Create page to render and grow the "Bind to session"
// picker without forcing the user to leave the page.

import { apiFetch } from "./client.js";

export function listSessions() {
  return apiFetch("/api/sessions");
}

export function createSession(name) {
  return apiFetch("/api/sessions", {
    method: "POST",
    body: { name },
  });
}

export function renameSession(id, name) {
  return apiFetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: { name },
  });
}

export function deleteSession(id) {
  return apiFetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
