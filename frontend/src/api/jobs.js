// Wrappers around the PR-13 job endpoints (design doc §6.3 / §6.4 / §16.2).
//
// `precheck` is JSON; `createJob` is multipart with a JSON `payload`
// form field plus zero or more `ref_<idx>` files. We construct the
// FormData here so callers don't have to remember the field-name
// contract documented in design doc §6.5.

import { apiFetch, getApiBase, getToken } from "./client.js";
import { isSilentErrorCode, messageForCode } from "../utils/errorCopy.js";

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

  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
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
    const message =
      mapped !== undefined && mapped !== null ? mapped : backendMessage;
    const err = new Error(message);
    err.status = res.status;
    err.code = code;
    err.field = field;
    err.data = data;
    err.silent = isSilentErrorCode(code);
    throw err;
  }
  return data;
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
