// Thin wrappers around /api/admin/announcements/*. Mirrors the backend
// router in `app/api/admin/announcements.py` per design doc §11+§13.
//
// Two POST flavours:
//   - createAnnouncement(payload)         → JSON, for text-kind announcements
//   - createImageAnnouncement(payload, file) → multipart for image-kind

import { apiFetch, getApiBase, getToken } from "../client.js";

const BASE = "/api/admin/announcements";

/** GET /api/admin/announcements — full list, newest first. */
export function listAnnouncements() {
  return apiFetch(BASE);
}

/** GET /api/admin/announcements/<id> — one row. */
export function getAnnouncement(id) {
  return apiFetch(`${BASE}/${encodeURIComponent(id)}`);
}

/** POST /api/admin/announcements (text) — JSON body. */
export function createTextAnnouncement(body) {
  return apiFetch(BASE, { method: "POST", body });
}

/**
 * POST /api/admin/announcements (image) — multipart with payload + cover.
 *
 * We bypass apiFetch here because it auto-stringifies bodies; multipart
 * needs a hand-built FormData. The auth header is grafted on manually.
 */
export async function createImageAnnouncement(body, coverFile) {
  const fd = new FormData();
  fd.append("payload", JSON.stringify(body));
  fd.append("cover", coverFile, coverFile.name || "cover.png");

  const url = `${getApiBase().replace(/\/+$/, "")}${BASE}`;
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(url, { method: "POST", body: fd, headers });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail = (data && typeof data === "object" && data.detail) || res.statusText;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || JSON.stringify(detail);
    const err = new Error(message);
    err.status = res.status;
    err.code = detail?.code || null;
    err.data = data;
    throw err;
  }
  return data;
}

/** PATCH /api/admin/announcements/<id> — partial update. */
export function patchAnnouncement(id, body) {
  return apiFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

/** DELETE /api/admin/announcements/<id>. */
export function deleteAnnouncement(id) {
  return apiFetch(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Convenience: build a cover preview URL (sets up auth header in img.src). */
export function coverUrl(id) {
  return `${getApiBase().replace(/\/+$/, "")}${BASE}/${encodeURIComponent(id)}/cover`;
}
