// Schema-driven param reconciliation.
//
// Walks every field declared by the model's ui_schema and drops or
// clamps values that fall outside the merged capabilities. Without
// this, switching from gpt-image-2 (size=1024x1024) to gemini-3.1-flash
// would leak the OpenAI-only `size` field into the gemini payload and
// trigger 422.
//
// `value_key` indirection: a field's `k` names the *capability* the
// cell reads from (e.g. n_max), but the actual *param* it writes to
// may be a different key (n). The fallback is `k` for the common case.
//
// Field-key universe: anything outside the schema's value_keys plus a
// hard-coded set of submission-glue keys (model / prompt / etc.) is
// considered junk left over from a previous model and gets dropped.
// Without this gate, default user-prefs like `aspect_ratio="1:1"`
// survive a switch to gpt-image-2 (which has no aspect_ratio field)
// and leak into the request payload.

import { paramKey } from "./paramKey.js";

export const SUBMISSION_GLUE_KEYS = new Set([
  "model",
  "prompt",
  "session_id",
  "client_request_id",
  "captcha_token",
]);

export function reconcileParams(params, capabilities, uiSchema) {
  const next = { ...params };
  const allowedParamKeys = new Set(SUBMISSION_GLUE_KEYS);
  for (const field of uiSchema || []) {
    allowedParamKeys.add(paramKey(field));
  }

  for (const k of Object.keys(next)) {
    if (!allowedParamKeys.has(k)) {
      delete next[k];
    }
  }

  for (const field of uiSchema || []) {
    const cap = capabilities?.[field.k];
    const pk = paramKey(field);
    const cur = next[pk];

    if (
      field.control === "chip-grid" ||
      field.control === "chip-row" ||
      field.control === "select"
    ) {
      // Three drop conditions for list fields:
      //   1. cap is an array AND current value isn't in it
      //   2. cap is an empty array (provider opted-out)
      //   3. cap is missing entirely (no provider exposes the field)
      // Cases 2 and 3 also render the field as disabled — without
      // dropping the value the request body would still ship a default
      // the merged caps don't permit, and the backend would reject
      // with INVALID_PARAMETER.
      if (cur != null) {
        if (!Array.isArray(cap) || cap.length === 0 || !cap.includes(cur)) {
          delete next[pk];
        }
      }
    } else if (field.control === "number") {
      if (typeof cur === "number" && typeof cap === "number" && cur > cap) {
        next[pk] = cap;
      }
    } else if (field.control === "toggle") {
      if (cur === true && cap !== true) {
        next[pk] = false;
      }
    }
  }
  return next;
}

export function applyDefaults(defaults, params, capabilities, uiSchema) {
  const next = { ...params };
  const setIfMissing = (key, fallback) => {
    if (next[key] === undefined || next[key] === null) next[key] = fallback;
  };
  Object.entries(defaults || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined) setIfMissing(k, v);
  });
  return reconcileParams(next, capabilities, uiSchema);
}
