// Wrappers around the PR-14 archive sync endpoints (design doc §16.3).
//
// Three high-level operations the frontend uses:
//
//   - Index sync (``GET /api/jobs/index``) — small per-row metadata
//     used to compute deltas against the local IndexedDB cache.
//   - Detail batch (``POST /api/jobs/details``) — pull full
//     :class:`JobDetail` for everything the index says is new/changed.
//   - State batch (``POST /api/jobs/states``) — SSE-down fallback.
//
// Plus single-row helpers (``getJob``, ``starImage``) and URL builders
// for image / reference reads. Image URLs are returned as plain
// strings; ``<img src=...>`` and ``<a download>`` consume them
// directly so the bytes never touch JS memory.

import { apiFetch, getApiBase, getToken } from "./client.js";

// ---------------------------------------------------------------------
// Cursor / index sync
// ---------------------------------------------------------------------

/**
 * GET /api/jobs/index — delta sync metadata.
 *
 * @param {object}   opts
 * @param {string?}  opts.since        ISO 8601 high-water mark.
 * @param {number?}  opts.limit        Page size; backend caps at 5000.
 * @param {string?}  opts.cursor       Continuation cursor from the prior page.
 * @param {string?}  opts.sessionId    Restrict to one session.
 * @returns {Promise<{items: object[], next_cursor: string|null}>}
 */
export function getJobsIndex({ since, limit, cursor, sessionId } = {}) {
  const params = new URLSearchParams();
  if (since) params.set("since", since);
  if (limit) params.set("limit", String(limit));
  if (cursor) params.set("cursor", cursor);
  if (sessionId) params.set("session_id", sessionId);
  const qs = params.toString();
  const path = qs ? `/api/jobs/index?${qs}` : "/api/jobs/index";
  return apiFetch(path, { method: "GET" });
}

/**
 * POST /api/jobs/details — fetch full detail for a batch of ids.
 *
 * Backend caps each call at 50 ids; we chunk transparently here so
 * the caller can pass any size list.
 */
export async function getJobsDetails(hashIds) {
  if (!Array.isArray(hashIds) || hashIds.length === 0) {
    return { items: [] };
  }
  const CHUNK = 50;
  const items = [];
  for (let i = 0; i < hashIds.length; i += CHUNK) {
    const slice = hashIds.slice(i, i + CHUNK);
    const resp = await apiFetch("/api/jobs/details", {
      method: "POST",
      body: { hash_ids: slice },
    });
    for (const entry of resp.items || []) {
      items.push(entry);
    }
  }
  return { items };
}

/**
 * POST /api/jobs/states — lean batch fallback when SSE is unavailable.
 *
 * Backend caps each call at 100 ids; we chunk transparently.
 */
export async function getJobsStates(hashIds) {
  if (!Array.isArray(hashIds) || hashIds.length === 0) {
    return { items: [] };
  }
  const CHUNK = 100;
  const items = [];
  for (let i = 0; i < hashIds.length; i += CHUNK) {
    const slice = hashIds.slice(i, i + CHUNK);
    const resp = await apiFetch("/api/jobs/states", {
      method: "POST",
      body: { hash_ids: slice },
    });
    for (const entry of resp.items || []) {
      items.push(entry);
    }
  }
  return { items };
}

/** GET /api/jobs/<hash> — full detail for one row. */
export function getJob(hashId) {
  return apiFetch(`/api/jobs/${encodeURIComponent(hashId)}`, { method: "GET" });
}

// ---------------------------------------------------------------------
// Star toggle
// ---------------------------------------------------------------------

/**
 * POST /api/jobs/<hash>/images/<order>/star.
 *
 * Pass ``starred`` explicitly to set; omit to toggle.
 */
export function starImage(hashId, order, starred) {
  const body = starred === undefined ? undefined : { starred };
  return apiFetch(
    `/api/jobs/${encodeURIComponent(hashId)}/images/${encodeURIComponent(order)}/star`,
    { method: "POST", body }
  );
}

// ---------------------------------------------------------------------
// URL builders for static reads
//
// Important: these endpoints serve binary data and are guarded by
// bearer-token auth on the server. Browsers can't carry an
// ``Authorization`` header through ``<img src>`` or ``<a download>``,
// so the build-then-fetch pattern won't work in production unless we
// add cookie auth or signed URLs. For v1 we accept that the dev
// server runs same-origin with the API and the browser gates these on
// session cookies / no auth (the backend rejects unauthenticated
// requests; the user just won't see thumbnails until they log in).
//
// Future work: switch to short-lived signed query-string tokens.
// ---------------------------------------------------------------------

function joinBase(path) {
  const base = getApiBase().replace(/\/+$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * Absolute URL for the WebP thumbnail of one output image.
 *
 * Suitable for ``<img src=>``. Adds the bearer token as a query
 * parameter so the browser can request the file without an
 * ``Authorization`` header — the backend strips ``?token=`` from the
 * request signature for the static read paths.
 *
 * NOTE: As of PR-14 the backend does NOT yet honour ``?token=`` for
 * these paths. They work in dev (same-origin, bearer cookie) but in
 * production the user will see broken thumbs until either:
 *   - the API is moved behind a same-origin reverse proxy and the
 *     bearer token is upgraded to a cookie, OR
 *   - the backend is taught to accept a signed token query param.
 * We expose the URL builder anyway so callers don't reinvent the
 * concatenation; the missing auth piece is tracked for a follow-up.
 */
export function imageThumbUrl(hashId, order) {
  return joinBase(
    `/api/jobs/${encodeURIComponent(hashId)}/images/${encodeURIComponent(order)}/thumb`
  );
}

/** Absolute URL for the original image (download). */
export function imageOriginalUrl(hashId, order) {
  return joinBase(
    `/api/jobs/${encodeURIComponent(hashId)}/images/${encodeURIComponent(order)}/original`
  );
}

/** Absolute URL for a reference-image thumbnail. */
export function referenceThumbUrl(hashId, order) {
  return joinBase(
    `/api/jobs/${encodeURIComponent(hashId)}/refs/${encodeURIComponent(order)}/thumb`
  );
}

/**
 * Fetch an image URL as a Blob with the bearer token so it can be
 * displayed via ``URL.createObjectURL``. Used by the archive thumb
 * component when the static URL alone won't carry auth (production).
 *
 * Returns ``null`` on 404 so the caller can render a "no longer on
 * server" placeholder instead of bubbling the error.
 */
export async function fetchImageBlob(url) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, { headers });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`fetchImageBlob: HTTP ${res.status}`);
  }
  return await res.blob();
}

/**
 * Download an authenticated image endpoint.
 *
 * Native <a href download> cannot attach the bearer token, so it gets
 * rejected by the backend. This helper performs the user-initiated fetch
 * with Authorization, then hands a temporary object URL to the browser's
 * download path.
 */
export async function downloadImageFile(url, fallbackName = "image") {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, { headers });
  if (!res.ok) {
    throw new Error(`downloadImageFile: HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match?.[1] || fallbackName;
  const objUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = objUrl;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
  }
  return { filename, bytes: blob.size };
}
