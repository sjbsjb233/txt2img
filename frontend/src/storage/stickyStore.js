// Sticky storage layer — saves the user's "long-lived" Create page
// preferences (last selected model + per-model parameter sets).
//
// Design doc §3.2: this layer is intentionally split from the Draft
// layer (prompt/refs/session) because sticky data outlives a successful
// generation. Clear button + Generate-success only reset Draft; sticky
// only resets on logout / account switch.

const STORAGE_KEY = "txt2img:create:sticky:v1";
const STICKY_VERSION = 1;

// Old draft key — used only by the v1 → v2 one-shot migration in
// migrateFromLegacyDraft(). The legacy draft has the same key shape but
// version 1 (with model_id + params bundled in).
const LEGACY_DRAFT_KEY = "txt2img:create:draft:v1";

export function readSticky(userId) {
  if (!userId) return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.v !== STICKY_VERSION) return null;
    if (parsed.user_id !== userId) return null;
    if (!parsed.params_by_model || typeof parsed.params_by_model !== "object") {
      parsed.params_by_model = {};
    }
    return parsed;
  } catch {
    return null;
  }
}

export function writeSticky(payload) {
  if (!payload || !payload.user_id) return false;
  try {
    const safe = {
      v: STICKY_VERSION,
      saved_at: Date.now(),
      user_id: payload.user_id,
      last_model_id: payload.last_model_id || null,
      params_by_model:
        payload.params_by_model && typeof payload.params_by_model === "object"
          ? payload.params_by_model
          : {},
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(safe));
    return true;
  } catch (err) {
    if (typeof console !== "undefined") {
      console.warn("sticky: localStorage write failed", err);
    }
    return false;
  }
}

export function clearSticky(userId) {
  // userId is accepted so future implementations can do per-user keys
  // without a signature change. Today we only have one slot.
  void userId;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignored
  }
}

// One-shot migration from draft v1 (model_id + params lived inside
// draft) to draft v2 (sticky owns model_id + params). Idempotent — once
// the legacy key is gone the function is a no-op.
//
// Returns { migrated: boolean }.
export function migrateFromLegacyDraft(userId) {
  if (!userId) return { migrated: false };
  let legacy;
  try {
    const raw = localStorage.getItem(LEGACY_DRAFT_KEY);
    if (!raw) return { migrated: false };
    legacy = JSON.parse(raw);
  } catch {
    return { migrated: false };
  }
  if (!legacy || legacy.v !== 1 || legacy.user_id !== userId) {
    return { migrated: false };
  }

  try {
    const existing = readSticky(userId);
    const params_by_model = existing?.params_by_model
      ? { ...existing.params_by_model }
      : {};
    if (
      legacy.model_id &&
      legacy.params &&
      typeof legacy.params === "object" &&
      Object.keys(legacy.params).length > 0
    ) {
      // Don't trample a sticky entry the user has already accumulated
      // for this model — only seed when we don't have one yet.
      if (!params_by_model[legacy.model_id]) {
        params_by_model[legacy.model_id] = legacy.params;
      }
    }
    writeSticky({
      user_id: userId,
      last_model_id: legacy.model_id || existing?.last_model_id || null,
      params_by_model,
    });

    // Promote the trimmed payload to draft v2 and drop the v1 key.
    const draftV2 = {
      v: 2,
      saved_at: legacy.saved_at || new Date().toISOString(),
      user_id: userId,
      session_id: legacy.session_id || null,
      prompt: legacy.prompt || "",
      refs_count: typeof legacy.refs_count === "number" ? legacy.refs_count : 0,
    };
    try {
      localStorage.setItem(
        "txt2img:create:draft:v2",
        JSON.stringify(draftV2)
      );
    } catch {
      // ignored
    }
    try {
      localStorage.removeItem(LEGACY_DRAFT_KEY);
    } catch {
      // ignored
    }
    return { migrated: true };
  } catch (err) {
    if (typeof console !== "undefined") {
      console.warn("sticky: migration failed", err);
    }
    return { migrated: false };
  }
}
