import { useCallback, useEffect, useRef, useState } from "react";
import * as draftDB from "../storage/draftDB.js";

const STORAGE_KEY = "txt2img:create:draft:v1";
const DRAFT_VERSION = 1;
const DEBOUNCE_MS = 600;
const VISIBLE_HOLD_SAVED_MS = 1600;
const VISIBLE_HOLD_CLEARED_MS = 1100;
const VISIBLE_HOLD_RESTORED_MS = 2800;
const FADE_OUT_MS = 360;
const COOLDOWN_MS = 4000;
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

function relativeMinutes(savedAtIso) {
  if (!savedAtIso) return "0M AGO";
  const t = Date.parse(savedAtIso);
  if (Number.isNaN(t)) return "0M AGO";
  const minutes = Math.max(0, Math.floor((Date.now() - t) / 60_000));
  if (minutes < 60) return `${minutes}M AGO`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}H AGO`;
  const days = Math.floor(hours / 24);
  return `${days}D AGO`;
}

function readLocalDraft() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.v !== DRAFT_VERSION) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeLocalDraft(draft) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
    return true;
  } catch (err) {
    // Quota / disabled / private mode — degrade silently.
    if (typeof console !== "undefined") {
      console.warn("draft autosave: localStorage write failed", err);
    }
    return false;
  }
}

function clearLocalDraft() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignored
  }
}

function paramsAreDefault(params) {
  if (!params || typeof params !== "object") return true;
  return Object.keys(params).length === 0;
}

function isDraftNonEmpty({ prompt, params, refsCount }) {
  if (prompt && prompt.trim().length > 0) return true;
  if (refsCount > 0) return true;
  if (!paramsAreDefault(params)) return true;
  return false;
}

/**
 * Drives autosave + restore for CreatePage. Returns the toast value
 * to render and a `clearDraft()` API the page wires to its Clear button
 * and to the post-Generate cleanup.
 */
export function useDraftAutosave({
  userId,
  prompt,
  params,
  refs,
  modelId,
  sessionId,
  catalog,
  enabled,
  onRestore,
}) {
  // Toast state — null when nothing should render. {kind, text, key, visible}.
  // ``visible`` toggles to drive the CSS opacity transition.
  const [toast, setToast] = useState(null);
  const toastKeyRef = useRef(0);
  const fadeOutTimerRef = useRef(null);
  const unmountTimerRef = useRef(null);
  const cooldownTimerRef = useRef(null);
  const inCooldownRef = useRef(false);
  const debounceTimerRef = useRef(null);
  const restoredRef = useRef(false);
  const hasWrittenRef = useRef(false);
  // After we hand a restored draft back to the page, the resulting
  // setState propagates to this hook's deps and would otherwise
  // (a) snap the just-shown RESTORED toast shut and (b) trigger a
  // redundant write of the draft we just read. Skip exactly one
  // change cycle after restore.
  const ignoreOnceRef = useRef(false);

  // Mirror the "live" form state so the beforeunload flush can read the
  // most-recent values without re-rendering the hook on every keystroke.
  const liveRef = useRef({ prompt, params, refs, modelId, sessionId });
  useEffect(() => {
    liveRef.current = { prompt, params, refs, modelId, sessionId };
  }, [prompt, params, refs, modelId, sessionId]);

  const onRestoreRef = useRef(onRestore);
  useEffect(() => {
    onRestoreRef.current = onRestore;
  }, [onRestore]);

  // ---------------------------------------------------------------
  // Toast helpers
  // ---------------------------------------------------------------

  const cancelToastTimers = useCallback(() => {
    if (fadeOutTimerRef.current) {
      clearTimeout(fadeOutTimerRef.current);
      fadeOutTimerRef.current = null;
    }
    if (unmountTimerRef.current) {
      clearTimeout(unmountTimerRef.current);
      unmountTimerRef.current = null;
    }
  }, []);

  const beginCooldown = useCallback(() => {
    inCooldownRef.current = true;
    if (cooldownTimerRef.current) clearTimeout(cooldownTimerRef.current);
    cooldownTimerRef.current = setTimeout(() => {
      inCooldownRef.current = false;
      cooldownTimerRef.current = null;
    }, COOLDOWN_MS);
  }, []);

  const startFadeOut = useCallback(() => {
    setToast((prev) => (prev ? { ...prev, visible: false } : prev));
    cancelToastTimers();
    unmountTimerRef.current = setTimeout(() => {
      setToast(null);
      unmountTimerRef.current = null;
      beginCooldown();
    }, FADE_OUT_MS);
  }, [beginCooldown, cancelToastTimers]);

  const showToast = useCallback(
    ({ kind, text, stayMs }) => {
      cancelToastTimers();
      toastKeyRef.current += 1;
      const key = toastKeyRef.current;
      // Mount visible — the component fades in via CSS @keyframes
      // (which run on element mount).
      setToast({ kind, text, key, visible: true });
      fadeOutTimerRef.current = setTimeout(() => {
        fadeOutTimerRef.current = null;
        setToast((cur) =>
          cur && cur.key === key ? { ...cur, visible: false } : cur
        );
        unmountTimerRef.current = setTimeout(() => {
          unmountTimerRef.current = null;
          setToast((cur) => (cur && cur.key === key ? null : cur));
          beginCooldown();
        }, FADE_OUT_MS);
      }, stayMs);
    },
    [beginCooldown, cancelToastTimers]
  );

  // Editing during a visible toast → snap it shut + go straight to cooldown.
  // Read ``toast`` from a ref so a fresh toast doesn't recreate this
  // callback, which would otherwise re-fire the autosave effect and
  // immediately snap the just-shown toast shut.
  const toastRef = useRef(null);
  useEffect(() => {
    toastRef.current = toast;
  }, [toast]);
  const onEditTouch = useCallback(() => {
    const cur = toastRef.current;
    if (!cur || !cur.visible) return;
    startFadeOut();
  }, [startFadeOut]);

  // ---------------------------------------------------------------
  // Write to disk (no toast)
  // ---------------------------------------------------------------

  const writeDraftNow = useCallback(async () => {
    if (!userId) return false;
    const live = liveRef.current;
    const refsCount = Array.isArray(live.refs) ? live.refs.length : 0;
    const draftIsEmpty = !isDraftNonEmpty({
      prompt: live.prompt,
      params: live.params,
      refsCount,
    });
    if (draftIsEmpty) {
      // Nothing meaningful to save — and if we'd written before, clear out.
      if (hasWrittenRef.current) {
        clearLocalDraft();
        try {
          await draftDB.clearRefs(userId);
        } catch {
          // ignored
        }
        hasWrittenRef.current = false;
      }
      return false;
    }
    const payload = {
      v: DRAFT_VERSION,
      saved_at: new Date().toISOString(),
      user_id: userId,
      model_id: live.modelId || null,
      session_id: live.sessionId || null,
      prompt: live.prompt || "",
      params: live.params || {},
      refs_count: refsCount,
    };
    const text = writeLocalDraft(payload);
    let idbOk = true;
    try {
      await draftDB.putRefs(userId, live.refs || []);
    } catch (err) {
      idbOk = false;
      if (typeof console !== "undefined") {
        console.warn("draft autosave: IDB write failed", err);
      }
    }
    hasWrittenRef.current = text || idbOk;
    return text || idbOk;
  }, [userId]);

  const writeDraftSync = useCallback(() => {
    // localStorage piece, sync. IDB is best-effort during unload.
    if (!userId) return;
    const live = liveRef.current;
    const refsCount = Array.isArray(live.refs) ? live.refs.length : 0;
    if (
      !isDraftNonEmpty({
        prompt: live.prompt,
        params: live.params,
        refsCount,
      })
    ) {
      return;
    }
    const payload = {
      v: DRAFT_VERSION,
      saved_at: new Date().toISOString(),
      user_id: userId,
      model_id: live.modelId || null,
      session_id: live.sessionId || null,
      prompt: live.prompt || "",
      params: live.params || {},
      refs_count: refsCount,
    };
    writeLocalDraft(payload);
    // Best-effort fire-and-forget IDB write — browsers may finish it
    // before the page fully unloads.
    draftDB.putRefs(userId, live.refs || []).catch(() => {});
  }, [userId]);

  // ---------------------------------------------------------------
  // Restore on mount
  // ---------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId || !catalog || restoredRef.current) return;
    restoredRef.current = true;
    (async () => {
      const draft = readLocalDraft();
      if (!draft) return;
      if (draft.user_id !== userId) return;
      const savedAtMs = Date.parse(draft.saved_at || "");
      if (Number.isFinite(savedAtMs) && Date.now() - savedAtMs > MAX_AGE_MS) {
        clearLocalDraft();
        try {
          await draftDB.clearRefs(userId);
        } catch {
          // ignored
        }
        return;
      }

      const allModels = catalog?.models || [];
      const modelStillThere =
        draft.model_id &&
        allModels.find(
          (m) => m.model_id === draft.model_id && m.available !== false
        );

      let refs = [];
      try {
        const idbRecord = await draftDB.getRefs(userId);
        if (idbRecord && Array.isArray(idbRecord.files)) {
          refs = idbRecord.files;
        }
      } catch (err) {
        if (typeof console !== "undefined") {
          console.warn("draft autosave: IDB read failed", err);
        }
      }
      if (refs.length !== (draft.refs_count || 0)) {
        if (typeof console !== "undefined") {
          console.warn(
            "draft autosave: refs_count mismatch — restoring text only"
          );
        }
        if (refs.length === 0 && draft.refs_count > 0) {
          // IDB lost — fall back to text-only restore.
        }
      }

      const restored = {
        prompt: draft.prompt || "",
        params: modelStillThere ? draft.params || {} : {},
        refs,
        modelId: modelStillThere ? draft.model_id : null,
        sessionId: draft.session_id || null,
      };

      const empty = !isDraftNonEmpty({
        prompt: restored.prompt,
        params: restored.params,
        refsCount: restored.refs.length,
      });
      if (empty) return;

      hasWrittenRef.current = true;
      ignoreOnceRef.current = true;
      const cb = onRestoreRef.current;
      if (cb) cb(restored);
      showToast({
        kind: "restored",
        text: `DRAFT RESTORED · ${relativeMinutes(draft.saved_at)}`,
        stayMs: VISIBLE_HOLD_RESTORED_MS,
      });
    })();
  }, [enabled, userId, catalog, showToast]);

  // ---------------------------------------------------------------
  // Debounced autosave on state change
  // ---------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId) return undefined;
    if (ignoreOnceRef.current) {
      ignoreOnceRef.current = false;
      return undefined;
    }
    // Touch the toast: if visible, snap it shut.
    onEditTouch();
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(async () => {
      debounceTimerRef.current = null;
      const wrote = await writeDraftNow();
      if (!wrote) return;
      if (inCooldownRef.current) return; // silent write
      showToast({
        kind: "saved",
        text: "DRAFT SAVED",
        stayMs: VISIBLE_HOLD_SAVED_MS,
      });
    }, DEBOUNCE_MS);
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
    };
  }, [
    enabled,
    userId,
    prompt,
    params,
    refs,
    modelId,
    sessionId,
    onEditTouch,
    showToast,
    writeDraftNow,
  ]);

  // ---------------------------------------------------------------
  // beforeunload — flush any pending debounce
  // ---------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId) return undefined;
    const flush = () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
        writeDraftSync();
      }
    };
    window.addEventListener("beforeunload", flush);
    return () => window.removeEventListener("beforeunload", flush);
  }, [enabled, userId, writeDraftSync]);

  // ---------------------------------------------------------------
  // Cleanup on unmount
  // ---------------------------------------------------------------

  useEffect(
    () => () => {
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      if (fadeOutTimerRef.current) clearTimeout(fadeOutTimerRef.current);
      if (unmountTimerRef.current) clearTimeout(unmountTimerRef.current);
      if (cooldownTimerRef.current) clearTimeout(cooldownTimerRef.current);
    },
    []
  );

  // ---------------------------------------------------------------
  // External clear
  // ---------------------------------------------------------------

  const clearDraft = useCallback(
    async ({ silent = false } = {}) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      clearLocalDraft();
      if (userId) {
        try {
          await draftDB.clearRefs(userId);
        } catch {
          // ignored
        }
      }
      hasWrittenRef.current = false;
      // Reset cooldown so a follow-up save reflects honestly. Clearing
      // is itself a user action; leave any cooldown timer alone (it'll
      // expire on its own and the explicit toast below bypasses it).
      if (silent) return;
      // User-initiated action → bypass cooldown.
      inCooldownRef.current = false;
      if (cooldownTimerRef.current) {
        clearTimeout(cooldownTimerRef.current);
        cooldownTimerRef.current = null;
      }
      showToast({
        kind: "cleared",
        text: "DRAFT CLEARED",
        stayMs: VISIBLE_HOLD_CLEARED_MS,
      });
    },
    [showToast, userId]
  );

  return { toast, clearDraft };
}
