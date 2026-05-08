/**
 * Lightbox — full-screen viewer for a single archive image.
 *
 * Renders into <body> via createPortal so the host page's
 * overflow:hidden never crops it. Wired up by the Archive drawer:
 * clicking the preview opens this; closing this leaves the drawer
 * open. prev/next navigates within the visible-singles list the page
 * already built.
 *
 * Gesture model (Mac-trackpad-first):
 *   - plain wheel (Δy dominant) at fit  = no-op (hint after second time)
 *   - plain wheel (Δy dominant) zoomed  = vertical pan
 *   - plain wheel (Δx dominant) at fit  = horizontal swipe → prev/next
 *   - plain wheel (Δx dominant) zoomed  = horizontal pan
 *   - wheel + ctrlKey                   = pinch zoom (Mac trackpad pinch)
 *   - wheel + metaKey                   = ⌘+wheel zoom (mouse fallback)
 *   - drag (pointer)                    = pan (only when zoomed)
 *   - dblclick                          = smart-zoom toggle
 *   - click empty stage at fit          = close
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  downloadImageFile,
  imageOriginalUrl,
  imageThumbUrl,
} from "../../api/archive.js";
import * as archiveStore from "../../store/archive.js";
import { aspectFromImage, shortModel } from "./archiveFilter.js";
import { fetchImageBlob } from "../../api/archive.js";

const MIN_SCALE = 1;
const MAX_SCALE = 5;

// Swipe-at-fit constants.
const SWIPE_COMMIT_DISTANCE = 80;        // px of accumulated horizontal delta before navigation fires
const SWIPE_COMMIT_VELOCITY = 0.5;       // alt path: > 0.5 px/ms with ≥30 px also commits
const SWIPE_SESSION_TIMEOUT = 180;       // ms with no new wheel event ⇒ session ended
const SWIPE_PEEK_RATIO = 0.7;            // visual offset is dampened against accumulated delta
const SWIPE_PEEK_MAX = 120;              // visual peek capped (keeps image on stage)
const INERTIA_IGNORE_MS = 600;           // after a commit, swallow inertia tail for this long
const RUBBER_BAND_DAMP = 0.5;            // pan past edges damped to this ratio
const RUBBER_BAND_MAX_PAST = 1.5;        // allow up to 1.5× of edge distance before snap

const HINT_DISMISS_KEY = "lightbox_hint_dismissed";

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function classifyWheel(e) {
  if (e.ctrlKey) return "pinch";        // Mac trackpad pinch (synth ctrl) OR Windows ctrl+wheel
  if (e.metaKey) return "cmd-zoom";     // ⌘ + wheel
  const ax = Math.abs(e.deltaX);
  const ay = Math.abs(e.deltaY);
  if (ax > ay * 1.2) return "swipe-x";
  if (ay > ax * 1.2) return "swipe-y";
  return "swipe-x";                     // ambiguous → favour horizontal intent
}

function relativeAge(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.floor(hr / 24);
  return `${d}d`;
}

function fmtSeconds(s) {
  if (s == null) return null;
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return `${m}m ${r}s`;
}

export default function Lightbox({
  open,
  row,
  position,
  total,
  onClose,
  onPrev,
  onNext,
  imageIndex = 0,
}) {
  // SET detail opens this for a specific panel inside a batch row;
  // singles always use index 0. Clamp so a stale index can't crash.
  const imgs = row?.images || [];
  const safeIdx = Math.max(0, Math.min(imageIndex, imgs.length - 1));
  const img = imgs[safeIdx] || imgs[0];
  const stageRef = useRef(null);
  const imgRef = useRef(null);

  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const [animateImg, setAnimateImg] = useState(false);
  const [closing, setClosing] = useState(false);
  const [toast, setToast] = useState(null);
  const [originalSrc, setOriginalSrc] = useState(null);
  const originalUrlRef = useRef(null);

  // Swipe-at-fit visual state.
  const [peek, setPeek] = useState(0);             // px image is offset horizontally during swipe
  const [edge, setEdge] = useState(null);          // 'first' | 'last' | null — boundary feedback band
  const [swipeDir, setSwipeDir] = useState(null);  // 'left' | 'right' | null — which gradient lights up
  const [swipeStrength, setSwipeStrength] = useState(0); // 0..1 progress toward commit

  // First-time hint.
  const [showHint, setShowHint] = useState(false);
  const [hintAtFitOnce, setHintAtFitOnce] = useState(false);

  // Refs that mirror state. We update them *imperatively* alongside
  // every setState so that a burst of synchronous wheel events (which
  // is exactly what a Mac trackpad produces) doesn't read stale values
  // before React has a chance to re-render.
  const scaleRef = useRef(scale);
  const translateRef = useRef(translate);
  const setScaleSync = useCallback((v) => { scaleRef.current = v; setScale(v); }, []);
  const setTranslateSync = useCallback((v) => { translateRef.current = v; setTranslate(v); }, []);
  // First-render mirror.
  scaleRef.current = scale;
  translateRef.current = translate;
  const positionRef = useRef(position);
  const totalRef = useRef(total);
  positionRef.current = position;
  totalRef.current = total;
  const onPrevRef = useRef(onPrev);
  const onNextRef = useRef(onNext);
  onPrevRef.current = onPrev;
  onNextRef.current = onNext;

  // Honour `prefers-reduced-motion: reduce` — disable the rubber-band
  // peek that appears mid-swipe (the per-image flip is still allowed,
  // but instant rather than animated; the snap-back is disabled too).
  const reducedMotionRef = useRef(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    reducedMotionRef.current = mq.matches;
    const onChange = () => { reducedMotionRef.current = mq.matches; };
    if (mq.addEventListener) {
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    }
    mq.addListener(onChange);
    return () => mq.removeListener(onChange);
  }, []);

  // Wheel session bookkeeping.
  const sessionRef = useRef({
    accum: 0,          // signed; right swipe (deltaX<0) accumulates positive
    lastT: 0,
    startT: 0,
    ignoreUntil: 0,
    snapTimer: null,   // for rubber-band snap when zoomed
    swipeTimer: null,  // for end-of-swipe-at-fit commit decision
  });

  // Reset transforms + swipe ephemeral state when the row OR active image changes.
  useEffect(() => {
    scaleRef.current = 1;
    translateRef.current = { x: 0, y: 0 };
    setScale(1);
    setTranslate({ x: 0, y: 0 });
    setPeek(0);
    setEdge(null);
    setSwipeDir(null);
    setSwipeStrength(0);
  }, [row?.hash_id, img?.order]);

  // Lock body scroll while open.
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // First-time hint.
  useEffect(() => {
    if (!open) return;
    let dismissed = false;
    try {
      dismissed = localStorage.getItem(HINT_DISMISS_KEY) === "1";
    } catch {
      // ignore (private mode)
    }
    if (dismissed) return;
    setShowHint(true);
    const t = setTimeout(() => setShowHint(false), 4000);
    return () => clearTimeout(t);
  }, [open]);

  const dismissHint = useCallback(() => {
    setShowHint(false);
    try {
      localStorage.setItem(HINT_DISMISS_KEY, "1");
    } catch {
      // ignore
    }
  }, []);

  // Toast helper.
  const flash = useCallback((msg) => {
    setToast(msg);
    const t = setTimeout(() => setToast(null), 1400);
    return () => clearTimeout(t);
  }, []);

  // Preload original — swap thumb for full-res when bytes arrive.
  useEffect(() => {
    if (!open || !row || !img) {
      setOriginalSrc(null);
      return undefined;
    }
    const url = imageOriginalUrl(row.hash_id, img.order);
    let cancelled = false;
    let blobUrl = null;
    setOriginalSrc(null);
    (async () => {
      try {
        const blob = await fetchImageBlob(url);
        if (cancelled || !blob) return;
        blobUrl = URL.createObjectURL(blob);
        if (originalUrlRef.current) {
          URL.revokeObjectURL(originalUrlRef.current);
        }
        originalUrlRef.current = blobUrl;
        setOriginalSrc(blobUrl);
      } catch {
        // Falling back to thumb is fine — the thumb is already shown.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, row?.hash_id, img?.order]);

  // Cleanup the last object URL on unmount.
  useEffect(() => {
    return () => {
      if (originalUrlRef.current) {
        URL.revokeObjectURL(originalUrlRef.current);
        originalUrlRef.current = null;
      }
    };
  }, []);

  // ----------------------------------------------------------------
  // Cursor-anchored zoom helpers.
  //
  // pinchZoomAt: pinch / cmd-zoom — uses exp(-Δy * 0.01) so that
  // pinch-open then pinch-closed returns to the original scale, and
  // the felt "speed" matches macOS.
  // ----------------------------------------------------------------
  const applyZoom = useCallback((nextScale, cursorX, cursorY) => {
    const stageEl = stageRef.current;
    if (!stageEl) return;
    const rect = stageEl.getBoundingClientRect();
    const cur = scaleRef.current;
    const ns = clamp(nextScale, MIN_SCALE, MAX_SCALE);
    if (ns === cur) return;
    const cx = (cursorX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2;
    const cy = (cursorY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2;
    const k = ns / cur;
    const tr = translateRef.current;
    let nx = cx - (cx - tr.x) * k;
    let ny = cy - (cy - tr.y) * k;
    if (ns <= 1.001) {
      // snap to centred fit when we collapse to scale 1
      nx = 0;
      ny = 0;
    }
    setScaleSync(ns);
    setTranslateSync({ x: nx, y: ny });
  }, [setScaleSync, setTranslateSync]);

  const pinchZoomAtCursor = useCallback((deltaY, cursorX, cursorY) => {
    const factor = Math.exp(-deltaY * 0.01);
    applyZoom(scaleRef.current * factor, cursorX, cursorY);
  }, [applyZoom]);

  // ----------------------------------------------------------------
  // Pan when zoomed. Uses sub-pixel deltas (don't round).
  // Soft-clamp via rubber band; snap back after a short idle.
  // ----------------------------------------------------------------
  const computePanBounds = useCallback(() => {
    const stageEl = stageRef.current;
    const imgEl = imgRef.current;
    if (!stageEl || !imgEl) return { maxX: 0, maxY: 0 };
    const sRect = stageEl.getBoundingClientRect();
    const iRect = imgEl.getBoundingClientRect();
    // iRect is the post-transform box; divide out the scale to get the
    // image's natural rendered size at scale = 1.
    const cur = scaleRef.current || 1;
    const baseW = iRect.width / cur;
    const baseH = iRect.height / cur;
    const half = (rectDim, baseDim) => Math.max(0, (baseDim * cur - rectDim) / 2);
    return {
      maxX: half(sRect.width, baseW),
      maxY: half(sRect.height, baseH),
    };
  }, []);

  const scheduleRubberBandSnap = useCallback(() => {
    const s = sessionRef.current;
    if (s.snapTimer) clearTimeout(s.snapTimer);
    s.snapTimer = setTimeout(() => {
      const { maxX, maxY } = computePanBounds();
      const tr = translateRef.current;
      const nx = clamp(tr.x, -maxX, maxX);
      const ny = clamp(tr.y, -maxY, maxY);
      if (nx !== tr.x || ny !== tr.y) {
        setAnimateImg(true);
        setTranslateSync({ x: nx, y: ny });
      }
    }, 150);
  }, [computePanBounds, setTranslateSync]);

  const panBy = useCallback((dx, dy) => {
    if (scaleRef.current <= 1.001) return;
    const { maxX, maxY } = computePanBounds();
    const tr = translateRef.current;
    const rawX = tr.x - dx;
    const rawY = tr.y - dy;
    const lo = (raw, max) => {
      if (max === 0) return raw * RUBBER_BAND_DAMP * 0.6;
      const overMax = max * RUBBER_BAND_MAX_PAST;
      if (raw < -max) {
        const past = Math.min(-raw - max, overMax);
        return -max - past * RUBBER_BAND_DAMP;
      }
      if (raw > max) {
        const past = Math.min(raw - max, overMax);
        return max + past * RUBBER_BAND_DAMP;
      }
      return raw;
    };
    setAnimateImg(false);
    setTranslateSync({ x: lo(rawX, maxX), y: lo(rawY, maxY) });
  }, [computePanBounds, setTranslateSync]);

  // ----------------------------------------------------------------
  // Horizontal swipe at fit → prev/next.
  // ----------------------------------------------------------------
  const animatePeekTo = useCallback((target) => {
    setAnimateImg(true);
    setPeek(target);
  }, []);

  const flyOutAndAdvance = useCallback((direction /* 'left' | 'right' */) => {
    setAnimateImg(true);
    const dir = direction === "left" ? -1 : 1;
    setPeek(dir * 220);             // animate off-stage in the same direction the user swiped
    const s = sessionRef.current;
    s.ignoreUntil = performance.now() + INERTIA_IGNORE_MS;
    setTimeout(() => {
      if (direction === "left") {
        onNextRef.current?.();
      } else {
        onPrevRef.current?.();
      }
      // The row/image change will reset peek via the useEffect above.
    }, 180);
  }, []);

  const handleHorizontalSwipeAtFit = useCallback((deltaX) => {
    const s = sessionRef.current;
    const now = performance.now();
    // New session?
    if (now - s.lastT > SWIPE_SESSION_TIMEOUT || s.startT === 0) {
      s.accum = 0;
      s.startT = now;
      setEdge(null);
    }
    // Right-swipe (want prev) on Mac trackpad surfaces deltaX < 0.
    s.accum -= deltaX;
    s.lastT = now;

    const pos = positionRef.current;
    const tot = totalRef.current;
    const atFirst = pos != null && pos <= 1;
    const atLast = pos != null && tot != null && pos >= tot;

    let visual = clamp(s.accum, -SWIPE_PEEK_MAX, SWIPE_PEEK_MAX) * SWIPE_PEEK_RATIO;

    // Boundary rubber-band: extra resistance.
    if (s.accum > 0 && atFirst) {
      visual = Math.min(visual, 30);
      setEdge("first");
    } else if (s.accum < 0 && atLast) {
      visual = Math.max(visual, -30);
      setEdge("last");
    } else {
      setEdge(null);
    }

    // Reduced motion: skip the visual peek entirely (the commit/snap
    // logic still runs — only the mid-gesture animation is suppressed).
    if (reducedMotionRef.current) visual = 0;

    setSwipeDir(s.accum < 0 ? "left" : s.accum > 0 ? "right" : null);
    const strength = clamp(Math.abs(s.accum) / SWIPE_COMMIT_DISTANCE, 0, 1);
    setSwipeStrength(strength);
    setAnimateImg(false);
    setPeek(visual);

    if (s.swipeTimer) clearTimeout(s.swipeTimer);
    s.swipeTimer = setTimeout(() => {
      const dur = s.lastT - s.startT;
      // Floor dur at 1 ms so a burst that arrives faster than
      // performance.now()'s resolution doesn't get treated as zero
      // velocity (which would silently bypass the velocity rule and
      // make fast small swipes flaky on slow CI machines).
      const velocity = Math.abs(s.accum) / Math.max(dur, 1);
      const accum = s.accum;
      // Reset session.
      s.accum = 0;
      s.startT = 0;
      s.lastT = 0;
      setSwipeStrength(0);
      setEdge(null);
      setSwipeDir(null);

      const wantNext = accum < -SWIPE_COMMIT_DISTANCE ||
                       (accum < -30 && velocity > SWIPE_COMMIT_VELOCITY);
      const wantPrev = accum > SWIPE_COMMIT_DISTANCE ||
                       (accum > 30 && velocity > SWIPE_COMMIT_VELOCITY);

      if (wantNext && !atLast) {
        flyOutAndAdvance("left");
      } else if (wantPrev && !atFirst) {
        flyOutAndAdvance("right");
      } else {
        animatePeekTo(0);
      }
    }, SWIPE_SESSION_TIMEOUT);
  }, [animatePeekTo, flyOutAndAdvance]);

  // ----------------------------------------------------------------
  // Wheel listener — single source of truth for trackpad gestures.
  // Bound only on `open` so the closure refs above always read live state.
  // ----------------------------------------------------------------
  useEffect(() => {
    if (!open) return undefined;
    const stageEl = stageRef.current;
    if (!stageEl) return undefined;
    const handler = (e) => {
      e.preventDefault();
      const now = performance.now();
      const s = sessionRef.current;
      if (now < s.ignoreUntil) return;       // swallow inertia tail right after a commit

      const cls = classifyWheel(e);
      if (cls === "pinch" || cls === "cmd-zoom") {
        setAnimateImg(false);
        pinchZoomAtCursor(e.deltaY, e.clientX, e.clientY);
        return;
      }
      if (scaleRef.current > 1.001) {
        // Zoomed: any plain wheel delta pans (both axes).
        panBy(e.deltaX, e.deltaY);
        scheduleRubberBandSnap();
        return;
      }
      // FIT state.
      if (cls === "swipe-x") {
        handleHorizontalSwipeAtFit(e.deltaX);
        return;
      }
      // swipe-y at fit → no-op + after the second time, suggest pinch.
      if (showHint || hintAtFitOnce === false) {
        setHintAtFitOnce(true);
      } else if (hintAtFitOnce && !showHint) {
        setShowHint(true);
        setTimeout(() => setShowHint(false), 2500);
      }
    };
    stageEl.addEventListener("wheel", handler, { passive: false });
    return () => stageEl.removeEventListener("wheel", handler);
  }, [open, pinchZoomAtCursor, panBy, scheduleRubberBandSnap, handleHorizontalSwipeAtFit, hintAtFitOnce, showHint]);

  // ----------------------------------------------------------------
  // Pointer drag (mouse + Magic Mouse fallback). Pans when zoomed.
  // At fit, a click without movement on the empty stage closes.
  // ----------------------------------------------------------------
  const dragStateRef = useRef({
    active: false,
    startX: 0,
    startY: 0,
    baseX: 0,
    baseY: 0,
    moved: 0,
    targetIsStage: false,
  });

  const onPointerDown = (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    const targetIsStage = e.target === stageRef.current;
    dragStateRef.current = {
      active: scale > 1.001,
      startX: e.clientX,
      startY: e.clientY,
      baseX: translate.x,
      baseY: translate.y,
      moved: 0,
      targetIsStage,
    };
    if (scale > 1.001) {
      e.preventDefault();
      setAnimateImg(false);
      e.currentTarget.setPointerCapture?.(e.pointerId);
    }
  };
  const onPointerMove = (e) => {
    const ds = dragStateRef.current;
    if (!ds.active) {
      // Track movement for click-to-close detection.
      const dx = e.clientX - ds.startX;
      const dy = e.clientY - ds.startY;
      ds.moved = Math.max(ds.moved, Math.hypot(dx, dy));
      return;
    }
    const dx = e.clientX - ds.startX;
    const dy = e.clientY - ds.startY;
    ds.moved = Math.max(ds.moved, Math.hypot(dx, dy));
    setTranslateSync({ x: ds.baseX + dx, y: ds.baseY + dy });
  };
  const onPointerUp = (e) => {
    const ds = dragStateRef.current;
    if (ds.active) {
      ds.active = false;
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    }
    // Click-to-close: only at fit, only if the pointerdown was on stage
    // background, with negligible movement.
    if (
      scaleRef.current <= 1.001 &&
      ds.targetIsStage &&
      ds.moved < 5 &&
      e.target === stageRef.current
    ) {
      handleClose();
    }
    dragStateRef.current = {
      active: false,
      startX: 0,
      startY: 0,
      baseX: 0,
      baseY: 0,
      moved: 0,
      targetIsStage: false,
    };
  };

  const onDoubleClick = (e) => {
    setAnimateImg(true);
    if (scale > 1.001) {
      // Reset to fit centred.
      setScaleSync(1);
      setTranslateSync({ x: 0, y: 0 });
    } else {
      // Smart zoom to 2× anchored on tap point.
      const stageEl = stageRef.current;
      if (!stageEl) return;
      const rect = stageEl.getBoundingClientRect();
      const cx = e.clientX - rect.left - rect.width / 2;
      const cy = e.clientY - rect.top - rect.height / 2;
      // For scale 1 → 2 with anchor cx, cy: tx = cx - (cx - 0) * 2 = -cx
      setScaleSync(2);
      setTranslateSync({ x: -cx, y: -cy });
    }
  };

  // ----------------------------------------------------------------
  // Toolbar / keyboard / housekeeping.
  // ----------------------------------------------------------------
  const fit = useCallback(() => {
    setAnimateImg(true);
    setScaleSync(1);
    setTranslateSync({ x: 0, y: 0 });
  }, [setScaleSync, setTranslateSync]);

  const oneToOne = useCallback(() => {
    const el = imgRef.current;
    const nat = img?.width;
    if (!el || !nat) return;
    const rect = el.getBoundingClientRect();
    const baseW = rect.width / (scaleRef.current || 1);
    if (!baseW) return;
    const target = nat / baseW;
    setAnimateImg(true);
    setScaleSync(clamp(target, MIN_SCALE, MAX_SCALE));
    setTranslateSync({ x: 0, y: 0 });
  }, [img?.width, setScaleSync, setTranslateSync]);

  const handleClose = useCallback(() => {
    setClosing(true);
    const t = setTimeout(() => {
      setClosing(false);
      onClose?.();
    }, 180);
    return () => clearTimeout(t);
  }, [onClose]);

  const toggleStar = useCallback(() => {
    if (!row || !img) return;
    void archiveStore.toggleStar(row.hash_id, img.order);
  }, [row, img]);

  const downloadCurrent = useCallback(() => {
    if (!row || !img) return;
    void downloadImageFile(
      imageOriginalUrl(row.hash_id, img.order),
      `${row.hash_id}_${String(img.order).padStart(2, "0")}.${img.format || "bin"}`
    );
    flash("download started");
  }, [row, img, flash]);

  const copyPrompt = useCallback(async () => {
    if (!row?.prompt) return;
    try {
      await navigator.clipboard?.writeText(row.prompt);
      flash("prompt copied");
    } catch {
      flash("copy failed");
    }
  }, [row?.prompt, flash]);

  // Keyboard shortcuts.
  useEffect(() => {
    if (!open) return undefined;
    const handler = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleClose();
      } else if (e.key === "[") {
        e.preventDefault();
        onPrev?.();
      } else if (e.key === "]") {
        e.preventDefault();
        onNext?.();
      } else if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        setAnimateImg(true);
        applyZoom(scaleRef.current * 1.25);
      } else if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        setAnimateImg(true);
        applyZoom(scaleRef.current / 1.25);
      } else if (e.key === "0") {
        e.preventDefault();
        fit();
      } else if (e.key === "1") {
        e.preventDefault();
        oneToOne();
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        toggleStar();
      } else if (e.key === "d" || e.key === "D") {
        e.preventDefault();
        downloadCurrent();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, handleClose, onPrev, onNext, applyZoom, fit, oneToOne, toggleStar, downloadCurrent]);

  if (!open || !row) return null;
  if (typeof document === "undefined") return null;

  const status = row.status || "";
  const statusColor =
    status === "SUCCEEDED" ? "var(--ok)" :
    status === "FAILED" ? "var(--bad)" :
    status === "RUNNING" ? "var(--banana)" :
    "var(--ink-3)";

  const ratio = aspectFromImage(img);
  const naturalRatio =
    img?.width && img?.height ? `${img.width} / ${img.height}` : "1 / 1";
  const dim = img ? `${img.width}×${img.height}` : "—";
  const queueSec = fmtSeconds(row.timing?.queue_seconds);
  const renderSec = fmtSeconds(row.timing?.render_seconds);
  const ageStr = relativeAge(row.updated_at);

  const stageGrabClass =
    scale > 1
      ? dragStateRef.current.active
        ? "lb-grabbing"
        : "lb-grab"
      : "";

  // Compose final image transform: include peek for the swipe-at-fit
  // visual reaction. peek is in viewport px and applied before scale.
  const tx = translate.x + (scale <= 1.001 ? peek : 0);
  const ty = translate.y;

  const node = (
    <div
      className={`lb-root ${closing ? "lb-closing" : ""}`.trim()}
      role="dialog"
      aria-modal="true"
      aria-label="Image preview"
      data-testid="lightbox"
    >
      {/* TOP NAV ----------------------------------------------------- */}
      <div className="lb-nav" data-testid="lightbox-topbar">
        <span className="lb-nav-tag">JOB</span>
        <span className="lb-nav-id" data-testid="lightbox-seq">#{row.seq_no ?? "—"}</span>
        <span
          className="lb-nav-dot"
          style={{ background: statusColor }}
          aria-label={status}
        />
        <span className="lb-nav-meta">
          {[status, shortModel(row.model), ratio, dim].filter(Boolean).join(" · ")}
        </span>
        <span className="lb-spacer" />

        <div className="lb-nav-group">
          <button
            type="button"
            className="lb-icon-btn"
            onClick={onPrev}
            disabled={position == null || position <= 1}
            aria-label="Previous image"
            data-testid="lightbox-prev-top"
          >
            ‹
          </button>
          <span className="lb-page-counter" data-testid="lightbox-page-indicator">
            {position != null && total != null ? `${position} / ${total}` : "—"}
          </span>
          <button
            type="button"
            className="lb-icon-btn"
            onClick={onNext}
            disabled={position == null || total == null || position >= total}
            aria-label="Next image"
            data-testid="lightbox-next-top"
          >
            ›
          </button>
        </div>

        <div className="lb-nav-group lb-actions">
          <button
            type="button"
            className="lb-icon-btn"
            onClick={toggleStar}
            aria-label={img?.starred ? "Unpick this image" : "Pick this image"}
            data-testid="lightbox-star"
            title="pick / unpick (s)"
          >
            {img?.starred ? "★" : "☆"}
          </button>
          <button
            type="button"
            className="lb-icon-btn"
            onClick={downloadCurrent}
            aria-label="Download original"
            data-testid="lightbox-download"
            title="download (d)"
          >
            ↓
          </button>
          <button
            type="button"
            className="lb-icon-btn"
            onClick={copyPrompt}
            aria-label="Copy prompt"
            data-testid="lightbox-copy"
            title="copy prompt"
          >
            ⧉
          </button>
        </div>

        <div className="lb-nav-group">
          <button
            type="button"
            className="lb-icon-btn"
            onClick={handleClose}
            aria-label="Close lightbox"
            data-testid="lightbox-close"
            title="close (Esc)"
          >
            ✕
          </button>
        </div>
      </div>

      {/* STAGE ------------------------------------------------------- */}
      <div
        ref={stageRef}
        className={`lb-stage ${stageGrabClass}`.trim()}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={onDoubleClick}
        data-testid="lightbox-stage"
        style={{ touchAction: "none", overscrollBehavior: "contain" }}
      >
        {/* Edge gradient bands (trackpad swipe visual feedback) */}
        <div
          className={[
            "lb-swipe-edge lb-swipe-edge-left",
            swipeDir === "right" ? "lb-on" : "",
            edge === "first" ? "lb-edge-block at-boundary" : "",
          ].join(" ").trim()}
          style={{ opacity: swipeDir === "right" ? 0.3 + 0.7 * swipeStrength : 0 }}
          data-testid="lightbox-rubber-left"
          aria-hidden="true"
        >
          {edge === "first" ? <span className="lb-edge-label">first</span> : null}
        </div>
        <div
          className={[
            "lb-swipe-edge lb-swipe-edge-right",
            swipeDir === "left" ? "lb-on" : "",
            edge === "last" ? "lb-edge-block at-boundary" : "",
          ].join(" ").trim()}
          style={{ opacity: swipeDir === "left" ? 0.3 + 0.7 * swipeStrength : 0 }}
          data-testid="lightbox-rubber-right"
          aria-hidden="true"
        >
          {edge === "last" ? <span className="lb-edge-label">last</span> : null}
        </div>

        {/* prev / next floating arrows */}
        <button
          type="button"
          className="lb-arrow lb-prev"
          onClick={onPrev}
          disabled={position == null || position <= 1}
          aria-label="Previous image"
          data-testid="lightbox-prev"
        >
          ‹
        </button>
        <button
          type="button"
          className="lb-arrow lb-next"
          onClick={onNext}
          disabled={position == null || total == null || position >= total}
          aria-label="Next image"
          data-testid="lightbox-next"
        >
          ›
        </button>

        {img ? (
          <img
            ref={imgRef}
            className={`lb-img ${animateImg ? "lb-anim" : ""}`.trim()}
            src={originalSrc || imageThumbUrl(row.hash_id, img.order)}
            alt=""
            draggable="false"
            data-testid="lightbox-image"
            data-original={originalSrc ? "1" : "0"}
            style={{
              aspectRatio: naturalRatio,
              transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
            }}
          />
        ) : (
          <div
            className="mono"
            style={{
              color: "#9c9488",
              fontSize: 11,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
            }}
          >
            no preview
          </div>
        )}

        {toast && <div className="lb-toast" data-testid="lightbox-toast">{toast}</div>}
      </div>

      {/* ZOOM CONTROLS ---------------------------------------------- */}
      <div className="lb-zoom" data-testid="lightbox-zoom">
        <button
          type="button"
          onClick={() => { setAnimateImg(true); applyZoom(scaleRef.current / 1.25); }}
          aria-label="Zoom out"
          data-testid="lightbox-zoom-out"
        >
          −
        </button>
        <span className="lb-zoom-readout" data-testid="lightbox-zoom-readout">
          {Math.round(scale * 100)}%
        </span>
        <button
          type="button"
          onClick={() => { setAnimateImg(true); applyZoom(scaleRef.current * 1.25); }}
          aria-label="Zoom in"
          data-testid="lightbox-zoom-in"
        >
          +
        </button>
        <button
          type="button"
          className="lb-zoom-fit"
          onClick={fit}
          aria-label="Fit"
          data-testid="lightbox-zoom-fit"
        >
          FIT
        </button>
        <button
          type="button"
          className="lb-zoom-fit-pct"
          onClick={oneToOne}
          aria-label="Actual size"
          data-testid="lightbox-zoom-100"
        >
          1:1
        </button>
      </div>

      {/* INFO STRIP -------------------------------------------------- */}
      <div className="lb-info" data-testid="lightbox-info">
        <span className="lb-info-label">PROMPT</span>
        <button
          type="button"
          className="lb-info-prompt"
          title={row.prompt || ""}
          onClick={copyPrompt}
          data-testid="lightbox-prompt"
        >
          {row.prompt || "—"}
        </button>
        <span className="lb-info-meta" data-testid="lightbox-meta">
          {ageStr ? <>
            <b>{ageStr}</b> ago
          </> : null}
          {queueSec ? <> · queue <b>{queueSec}</b></> : null}
          {renderSec ? <> · render <b>{renderSec}</b></> : null}
        </span>
      </div>

      {showHint ? (
        <div className="lb-hint" data-testid="lightbox-hint">
          <span>two-finger pinch to zoom · swipe ↔ to navigate · ⌘ + scroll for zoom</span>
          <button
            type="button"
            className="lb-hint-x"
            onClick={dismissHint}
            aria-label="Dismiss hint"
            data-testid="lightbox-hint-dismiss"
          >
            ✕
          </button>
        </div>
      ) : null}
    </div>
  );

  return createPortal(node, document.body);
}
