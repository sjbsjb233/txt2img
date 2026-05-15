// Shared param utilities used by both Create and MaskEdit pages so the
// "what does the active model accept?" reconciliation logic lives in
// exactly one place. Pure functions only — no React; the hook variant
// (``useFieldRenderPlan``) wraps these for memoization in the consumer.
//
// Extracted from CreatePage.jsx so MaskEditPage can drive its own
// param payload from the model's ui_schema + capabilities instead of
// hard-coding 4 fields and praying they survive a model swap.

export const SUBMISSION_GLUE_KEYS = new Set([
  "model",
  "prompt",
  "session_id",
  "client_request_id",
  "captcha_token",
]);

export function paramKey(field) {
  return field.value_key || field.k;
}

// Drop params that aren't in the schema and clamp/trim values that
// don't match capabilities. Mirrors the reconcile semantics CreatePage
// uses on every model swap.
export function reconcileParams(params, capabilities, uiSchema) {
  const next = { ...(params || {}) };
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
      const isCustomSizeKeep =
        field.k === "size" &&
        capabilities?.size_allow_custom === true &&
        typeof cur === "string" &&
        /^\d+x\d+$/.test(cur);
      if (
        cur != null &&
        !isCustomSizeKeep &&
        (!Array.isArray(cap) || !cap.includes(cur))
      ) {
        delete next[pk];
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
  const next = { ...(params || {}) };
  const setIfMissing = (key, fallback) => {
    if (next[key] === undefined || next[key] === null) next[key] = fallback;
  };
  Object.entries(defaults || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined) setIfMissing(k, v);
  });
  return reconcileParams(next, capabilities, uiSchema);
}
