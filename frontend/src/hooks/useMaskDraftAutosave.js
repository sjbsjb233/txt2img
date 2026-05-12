import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as maskDraftDB from "../storage/maskDraftDB.js";
import { countMaskPaintedPixels } from "../components/maskeditor/utils/maskExport.js";

const SCHEMA_V = 2;
const DEBOUNCE_MS = 800;
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

const VISIBLE_HOLD_SAVED_MS = 1600;
const VISIBLE_HOLD_RESTORED_MS = 2800;
const FADE_OUT_MS = 360;
const COOLDOWN_MS = 4000;

function relativeAgo(savedAtIso) {
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

const DEFAULT_OUTPAINT = { directions: ["right"], amount: "25%" };

function isOutpaintDefault(o) {
  if (!o) return true;
  const dirs = Array.isArray(o.directions) ? o.directions : [];
  return (
    dirs.length === 1 &&
    dirs[0] === DEFAULT_OUTPAINT.directions[0] &&
    o.amount === DEFAULT_OUTPAINT.amount
  );
}

/**
 * In-memory snapshot of "is there anything worth saving right now?"
 * Note: ``hasPaint`` requires actually inspecting the canvas; the
 * caller hands us ``markCanvasDirty`` and we re-check on debounce fire.
 */
function recordIsEmpty({ prompt, refsCount, hasPaint, outpaintMode, outpaint }) {
  const trimmed = (prompt || "").trim();
  if (trimmed.length > 0) return false;
  if (refsCount > 0) return false;
  if (hasPaint) return false;
  if (outpaintMode && !isOutpaintDefault(outpaint)) return false;
  return true;
}

async function canvasToPngBlob(canvas) {
  if (!canvas) return null;
  return await new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob || null), "image/png");
    } catch {
      resolve(null);
    }
  });
}

/**
 * Drives autosave + restore for the MaskEditPage. Mirrors the surface
 * of useDraftAutosave but the payload is per-image (composite key
 * built from URL params + mode), and the canvas is part of the saved
 * state instead of just text + refs.
 *
 * The page passes everything it owns (prompt, refs, brushOpts, etc.)
 * — we serialize on debounce, write to ``maskDraftDB``, and surface a
 * toast value the page can render. Restoration is the inverse: on
 * mount, if a record exists, we hand it back via ``onRestore`` and
 * the page is responsible for stuffing the values back into state +
 * painting the mask blob onto its canvas.
 */
export function useMaskDraftAutosave({
  userId,
  hashId,
  order,
  mode,
  enabled,
  prompt,
  refs,
  brushOpts,
  advanced,
  outpaint,
  outpaintMode,
  activeTool,
  activeTab,
  maskCanvas,
  sourceJob,
  status,
  imageW,
  imageH,
  onRestore,
}) {
  const draftId = useMemo(
    () => (hashId && order ? maskDraftDB.makeDraftId(hashId, order, mode) : null),
    [hashId, order, mode]
  );

  // Toast state — null when nothing should render.
  const [toast, setToast] = useState(null);
  const [hydrated, setHydrated] = useState(false);
  const toastKeyRef = useRef(0);
  const fadeOutTimerRef = useRef(null);
  const unmountTimerRef = useRef(null);
  const cooldownTimerRef = useRef(null);
  const inCooldownRef = useRef(false);
  const debounceTimerRef = useRef(null);
  const restoredRef = useRef(false);
  // After the page consumes a restore, its setStates ripple back here
  // and would (a) hide the just-shown RESTORED toast and (b) re-write
  // the very record we just read. Skip exactly one cycle.
  const ignoreOnceRef = useRef(false);
  const hasWrittenRef = useRef(false);
  // Hold the most recently serialized mask Blob so beforeunload can
  // flush without paying another toBlob hit.
  const lastMaskBlobRef = useRef(null);
  const maskDirtyRef = useRef(false);

  const liveRef = useRef({
    prompt,
    refs,
    brushOpts,
    advanced,
    outpaint,
    outpaintMode,
    activeTool,
    activeTab,
    maskCanvas,
    sourceJob,
    imageW,
    imageH,
  });
  useEffect(() => {
    liveRef.current = {
      prompt,
      refs,
      brushOpts,
      advanced,
      outpaint,
      outpaintMode,
      activeTool,
      activeTab,
      maskCanvas,
      sourceJob,
      imageW,
      imageH,
    };
  }, [
    prompt,
    refs,
    brushOpts,
    advanced,
    outpaint,
    outpaintMode,
    activeTool,
    activeTab,
    maskCanvas,
    sourceJob,
    imageW,
    imageH,
  ]);

  const onRestoreRef = useRef(onRestore);
  useEffect(() => {
    onRestoreRef.current = onRestore;
  }, [onRestore]);

  // -------------------------------------------------------------
  // Toast helpers
  // -------------------------------------------------------------

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

  // -------------------------------------------------------------
  // Mask-canvas dirty marking
  // -------------------------------------------------------------

  const markCanvasDirty = useCallback(() => {
    maskDirtyRef.current = true;
  }, []);

  // -------------------------------------------------------------
  // Build + write current snapshot
  // -------------------------------------------------------------

  const buildRecord = useCallback(async () => {
    if (!draftId) return null;
    const live = liveRef.current;
    const refsCount = Array.isArray(live.refs) ? live.refs.length : 0;
    const hasPaint = !!live.maskCanvas && countMaskPaintedPixels(live.maskCanvas) > 0;

    if (
      recordIsEmpty({
        prompt: live.prompt,
        refsCount,
        hasPaint,
        outpaintMode: live.outpaintMode,
        outpaint: live.outpaint,
      })
    ) {
      return null;
    }

    let maskBlob = lastMaskBlobRef.current;
    if (live.maskCanvas && (maskDirtyRef.current || !maskBlob)) {
      const blob = await canvasToPngBlob(live.maskCanvas);
      if (blob) {
        maskBlob = blob;
        lastMaskBlobRef.current = blob;
        maskDirtyRef.current = false;
      }
    }
    if (!hasPaint) {
      // No paint anymore (user cleared the mask) — drop any blob we cached.
      maskBlob = null;
      lastMaskBlobRef.current = null;
    }

    const refsFiles = Array.isArray(live.refs)
      ? live.refs
          .map((r) => (r && r.file ? r.file : null))
          .filter((f) => f instanceof Blob)
      : [];

    return {
      draft_id: draftId,
      parent_hash_id: hashId,
      order: Number(order) || 1,
      mode: mode === "outpaint" ? "outpaint" : "inpaint",
      saved_at: new Date().toISOString(),
      source_seq_no: live.sourceJob?.seq_no ?? null,
      source_model: live.sourceJob?.model || null,
      schema_v: SCHEMA_V,
      mask_blob: maskBlob || null,
      mask_w: live.maskCanvas?.width || live.imageW || 0,
      mask_h: live.maskCanvas?.height || live.imageH || 0,
      has_paint: hasPaint,
      prompt: live.prompt || "",
      refs: refsFiles,
      brush_opts: { ...(live.brushOpts || {}) },
      advanced: { ...(live.advanced || {}) },
      outpaint: { ...(live.outpaint || {}) },
      active_tool: live.activeTool || null,
      active_tab: live.activeTab || null,
    };
  }, [draftId, hashId, order, mode]);

  const writeDraftNow = useCallback(async () => {
    if (!userId || !draftId) return false;
    const record = await buildRecord();
    if (!record) {
      // Nothing meaningful to save — clean any prior record.
      if (hasWrittenRef.current) {
        try {
          await maskDraftDB.deleteDraft(userId, draftId);
        } catch {
          // ignored
        }
        hasWrittenRef.current = false;
      }
      return false;
    }
    try {
      await maskDraftDB.putDraft(userId, record);
      hasWrittenRef.current = true;
      return true;
    } catch (err) {
      if (typeof console !== "undefined") {
        console.warn("mask draft autosave: IDB write failed", err);
      }
      return false;
    }
  }, [userId, draftId, buildRecord]);

  // Synchronous-ish flush for ``beforeunload``. We can't await IDB
  // here without blocking the unload, so we fire-and-forget; the
  // most recently cached mask blob (already serialized during normal
  // editing) is what makes this practical.
  const writeDraftSync = useCallback(() => {
    if (!userId || !draftId) return;
    const live = liveRef.current;
    const refsCount = Array.isArray(live.refs) ? live.refs.length : 0;
    const hasPaint =
      !!live.maskCanvas && countMaskPaintedPixels(live.maskCanvas) > 0;

    if (
      recordIsEmpty({
        prompt: live.prompt,
        refsCount,
        hasPaint,
        outpaintMode: live.outpaintMode,
        outpaint: live.outpaint,
      })
    ) {
      if (hasWrittenRef.current) {
        maskDraftDB.deleteDraft(userId, draftId).catch(() => {});
        hasWrittenRef.current = false;
      }
      return;
    }
    const refsFiles = Array.isArray(live.refs)
      ? live.refs
          .map((r) => (r && r.file ? r.file : null))
          .filter((f) => f instanceof Blob)
      : [];
    const record = {
      draft_id: draftId,
      parent_hash_id: hashId,
      order: Number(order) || 1,
      mode: mode === "outpaint" ? "outpaint" : "inpaint",
      saved_at: new Date().toISOString(),
      source_seq_no: live.sourceJob?.seq_no ?? null,
      source_model: live.sourceJob?.model || null,
      schema_v: SCHEMA_V,
      mask_blob: hasPaint ? lastMaskBlobRef.current || null : null,
      mask_w: live.maskCanvas?.width || live.imageW || 0,
      mask_h: live.maskCanvas?.height || live.imageH || 0,
      has_paint: hasPaint,
      prompt: live.prompt || "",
      refs: refsFiles,
      brush_opts: { ...(live.brushOpts || {}) },
      advanced: { ...(live.advanced || {}) },
      outpaint: { ...(live.outpaint || {}) },
      active_tool: live.activeTool || null,
      active_tab: live.activeTab || null,
    };
    maskDraftDB.putDraft(userId, record).catch(() => {});
  }, [userId, draftId, hashId, order, mode]);

  // -------------------------------------------------------------
  // Restore on mount
  // -------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId || !draftId || restoredRef.current) return;
    restoredRef.current = true;
    (async () => {
      let record = null;
      try {
        record = await maskDraftDB.getDraft(userId, draftId, {
          maxAgeMs: MAX_AGE_MS,
        });
      } catch (err) {
        if (typeof console !== "undefined") {
          console.warn("mask draft autosave: IDB read failed", err);
        }
      }
      setHydrated(true);
      if (!record) return;

      hasWrittenRef.current = true;
      ignoreOnceRef.current = true;
      const cb = onRestoreRef.current;
      if (cb) {
        try {
          cb(record);
        } catch (err) {
          if (typeof console !== "undefined") {
            console.warn("mask draft autosave: onRestore failed", err);
          }
        }
      }
      showToast({
        kind: "restored",
        text: `DRAFT RESTORED · ${relativeAgo(record.saved_at)}`,
        stayMs: VISIBLE_HOLD_RESTORED_MS,
      });
    })();
  }, [enabled, userId, draftId, showToast]);

  // -------------------------------------------------------------
  // Debounced autosave on dependency change
  // -------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId || !draftId) return undefined;
    if (ignoreOnceRef.current) {
      ignoreOnceRef.current = false;
      return undefined;
    }
    if (status === "submitting" || status === "running") return undefined;

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
    draftId,
    status,
    prompt,
    refs,
    brushOpts,
    advanced,
    outpaint,
    outpaintMode,
    activeTool,
    activeTab,
    showToast,
    writeDraftNow,
  ]);

  // -------------------------------------------------------------
  // beforeunload — flush
  // -------------------------------------------------------------

  useEffect(() => {
    if (!enabled || !userId || !draftId) return undefined;
    const flush = () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      writeDraftSync();
    };
    window.addEventListener("beforeunload", flush);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("beforeunload", flush);
      window.removeEventListener("pagehide", flush);
    };
  }, [enabled, userId, draftId, writeDraftSync]);

  // -------------------------------------------------------------
  // Cleanup on unmount
  // -------------------------------------------------------------

  useEffect(
    () => () => {
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      if (fadeOutTimerRef.current) clearTimeout(fadeOutTimerRef.current);
      if (unmountTimerRef.current) clearTimeout(unmountTimerRef.current);
      if (cooldownTimerRef.current) clearTimeout(cooldownTimerRef.current);
    },
    []
  );

  // Best-effort flush when the page is unmounting (Layout swap, route
  // change). beforeunload only fires for tab close / refresh.
  useEffect(() => {
    if (!enabled || !userId || !draftId) return undefined;
    return () => {
      if (debounceTimerRef.current) {
        // Pending debounce was queued — try one final write.
        try {
          writeDraftSync();
        } catch {
          // ignored
        }
      }
    };
  }, [enabled, userId, draftId, writeDraftSync]);

  // -------------------------------------------------------------
  // External clear
  // -------------------------------------------------------------

  const clearDraft = useCallback(
    async ({ silent = false } = {}) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      if (userId && draftId) {
        try {
          await maskDraftDB.deleteDraft(userId, draftId);
        } catch {
          // ignored
        }
      }
      hasWrittenRef.current = false;
      lastMaskBlobRef.current = null;
      maskDirtyRef.current = false;
      if (silent) return;
      // Surface a quick "cleared" toast for explicit user-driven clears.
      inCooldownRef.current = false;
      if (cooldownTimerRef.current) {
        clearTimeout(cooldownTimerRef.current);
        cooldownTimerRef.current = null;
      }
      showToast({
        kind: "cleared",
        text: "DRAFT CLEARED",
        stayMs: 1100,
      });
    },
    [showToast, userId, draftId]
  );

  return { toast, clearDraft, hydrated, markCanvasDirty };
}
