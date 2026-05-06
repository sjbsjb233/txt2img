import { useCallback, useEffect, useRef, useState } from "react";
import * as draftDB from "../storage/draftDB.js";

// Draft v2 — model_id and params have moved into the Sticky layer
// (txt2img:create:sticky:v1). This payload now only owns the ephemeral
// trio that should be wiped on Generate-success / Clear / 7-day expiry.
const STORAGE_KEY = "txt2img:create:draft:v2";
const DRAFT_VERSION = 2;
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

function isDraftNonEmpty({ prompt, refsCount, sessionId }) {
  if (prompt && prompt.trim().length > 0) return true;
  if (refsCount > 0) return true;
  if (sessionId) return true;
  return false;
}

/**
 * Drives autosave + restore for the *ephemeral* part of CreatePage state
 * (prompt, references, bound session). Returns the toast value to render
 * and a `clearDraft()` API the page wires to its Clear button and to the
 * post-Generate cleanup.
 *
 * Model + params persistence is handled separately by useStickyState —
 * see frontend/src/hooks/useStickyState.js.
 */
export function useDraftAutosave({
  userId,
  prompt,
  refs,
  sessionId,
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
  const liveRef = useRef({ prompt, refs, sessionId });
  useEffect(() => {
    liveRef.current = { prompt, refs, sessionId };
  }, [prompt, refs, sessionId]);

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
      refsCount,
      sessionId: live.sessionId,
    });
    if (draftIsEmpty) {
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
      session_id: live.sessionId || null,
      prompt: live.prompt || "",
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
    if (!userId) return;
    const live = liveRef.current;
    const refsCount = Array.isArray(live.refs) ? live.refs.length : 0;
    if (
      !isDraftNonEmpty({
        prompt: live.prompt,
        refsCount,
        sessionId: live.sessionId,
      })
    ) {
      if (hasWrittenRef.current) {
        clearLocalDraft();
        draftDB.clearRefs(userId).catch(() => {});
        hasWrittenRef.current = false;
      }
      return;
    }
    const payload = {
      v: DRAFT_VERSION,
      saved_at: new Date().toISOString(),
      user_id: userId,
      session_id: live.sessionId || null,
      prompt: live.prompt || "",
      refs_count: refsCount,
    };
    writeLocalDraft(payload);
    draftDB.putRefs(userId, live.refs || []).catch(() => {});
  }, [userId]);

  // ---------------------------------------------------------------
  // Restore on mount
  // ---------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId || restoredRef.current) return;
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
        refs = [];
        if (userId) {
          draftDB.clearRefs(userId).catch(() => {});
        }
      }

      const restored = {
        prompt: draft.prompt || "",
        refs,
        sessionId: draft.session_id || null,
      };

      const empty = !isDraftNonEmpty({
        prompt: restored.prompt,
        refsCount: restored.refs.length,
        sessionId: restored.sessionId,
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
  }, [enabled, userId, showToast]);

  // ---------------------------------------------------------------
  // Debounced autosave on state change
  // ---------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId) return undefined;
    if (ignoreOnceRef.current) {
      ignoreOnceRef.current = false;
      return undefined;
    }
    onEditTouch();
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(async () => {
      debounceTimerRef.current = null;
      const wrote = await writeDraftNow();
      if (!wrote) return;
      if (inCooldownRef.current) return;
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
    refs,
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
  // External clear — wipes only the ephemeral layer (prompt/refs/session).
  // Sticky (model + per-model params) is intentionally untouched here;
  // see design doc §4.2 / §4.8.
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
      if (silent) return;
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
