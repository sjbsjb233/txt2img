// Wrappers around the PR-13 job endpoints (design doc §6.3 / §6.4 / §16.2).
//
// `precheck` is JSON; `createJob` is multipart with a JSON `payload`
// form field plus zero or more `ref_<idx>` files. We construct the
// FormData here so callers don't have to remember the field-name
// contract documented in design doc §6.5.

import { apiFetch, getApiBase, getToken } from "./client.js";
import { isSilentErrorCode, messageForCode } from "../utils/errorCopy.js";

// Parse a multipart-job response identically across endpoints so error
// UX (i18n via messageForCode, silent codes, field-targeted hints)
// doesn't drift between createJob and replaceJobImage. Throws on
// non-2xx; returns parsed JSON otherwise.
async function _readMultipartJobResponse(res) {
  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (res.ok) return data;

  const detail =
    (data && typeof data === "object" && data.detail) ||
    res.statusText ||
    `HTTP ${res.status}`;
  let backendMessage;
  let code = null;
  let field = null;
  if (typeof detail === "string") {
    backendMessage = detail;
  } else if (detail && typeof detail === "object") {
    backendMessage =
      typeof detail.message === "string"
        ? detail.message
        : JSON.stringify(detail);
    code = typeof detail.code === "string" ? detail.code : null;
    field = typeof detail.field === "string" ? detail.field : null;
  } else {
    backendMessage = String(detail);
  }
  const mapped = messageForCode(code);
  const err = new Error(
    mapped !== undefined && mapped !== null ? mapped : backendMessage
  );
  err.status = res.status;
  err.code = code;
  err.field = field;
  err.data = data;
  err.silent = isSilentErrorCode(code);
  throw err;
}

/**
 * POST /api/jobs/precheck
 * Returns `{captcha_required, captcha_provider, site_key, reason}`.
 */
export function precheck({ model }) {
  return apiFetch("/api/jobs/precheck", {
    method: "POST",
    body: { model },
  });
}

/**
 * POST /api/jobs (multipart). `payload` carries the JSON body; each
 * entry in `references` becomes one `ref_<idx>` file part, in array
 * order — that's the contract the backend uses to seed `01_*`,
 * `02_*`... filenames on disk (design doc §6.5).
 *
 * apiFetch already handles auth + force-logout interception, but it
 * does not support multipart bodies on its own. We stage a thin
 * fetch() here and run the response through the same error envelope.
 */
export async function createJob({ payload, references = [], mask = null }) {
  const body = new FormData();
  // ``payload`` is a plain JSON string carried in a text form field —
  // appending a Blob here would make Starlette decode it as an
  // ``UploadFile`` and FastAPI's ``payload: str = Form(...)`` would
  // refuse it as the wrong type.
  body.append("payload", JSON.stringify(payload));
  references.forEach((file, idx) => {
    body.append(`ref_${idx}`, file, file.name || `ref_${idx + 1}`);
  });
  if (mask) {
    // mask is a Blob/File — single PNG with alpha channel. Backend
    // recognises field name "mask" via _collect_attachments.
    body.append("mask", mask, mask.name || "mask.png");
  }

  const url = `${getApiBase().replace(/\/+$/, "")}/api/jobs`;
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(url, { method: "POST", body, headers });
  } catch (e) {
    throw new Error(`network error: ${e.message || e}`);
  }
  return await _readMultipartJobResponse(res);
}

/**
 * POST /api/jobs/<hash>/cancel — cancel a QUEUED or RUNNING job.
 */
export function cancelJob(hashId) {
  return apiFetch(`/api/jobs/${encodeURIComponent(hashId)}/cancel`, {
    method: "POST",
  });
}

/**
 * DELETE /api/jobs/<hash> — soft-delete a terminal job.
 */
export function deleteJob(hashId) {
  return apiFetch(`/api/jobs/${encodeURIComponent(hashId)}`, {
    method: "DELETE",
  });
}

/**
 * POST /api/jobs/<hash>/replace_image (multipart).
 *
 * Composite a mask-only edit result back into the source image and
 * upload the merged PNG. Used by the compare-mode "accept changes"
 * flow when the user picks the ``mask-only`` preservation strategy.
 *
 * Backend backs up the original next to it (``01_*.png.original`` —
 * keeps the original suffix as the sentinel for idempotency) and
 * updates ``meta.json`` with ``images[0].composite = "mask_only_overlay"``.
 * The call is one-shot: a second attempt returns 409.
 */
export async function replaceJobImage(hashId, composite, strategy = "mask_only_overlay") {
  const body = new FormData();
  body.append("composite", composite, composite.name || "composite.png");
  body.append("strategy", strategy);

  const url = `${getApiBase().replace(/\/+$/, "")}/api/jobs/${encodeURIComponent(hashId)}/replace_image`;
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(url, { method: "POST", body, headers });
  } catch (e) {
    throw new Error(`network error: ${e.message || e}`);
  }
  return await _readMultipartJobResponse(res);
}
