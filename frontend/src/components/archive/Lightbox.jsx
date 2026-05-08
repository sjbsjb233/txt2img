/**
 * Lightbox — full-screen viewer for a single archive image.
 *
 * Renders into <body> via createPortal so the host page's
 * overflow:hidden never crops it. Wired up by the Archive drawer:
 * clicking the preview opens this; closing this leaves the drawer
 * open. prev/next navigates within the visible-singles list the page
 * already built.
 *
 * Zoom/pan model:
 *   - scale ∈ [0.25, 4]; default 1 (FIT)
 *   - wheel zoom is cursor-anchored (the image point under the cursor
 *     stays put)
 *   - drag-to-pan only when scale > 1; no boundary clamp — FIT button
 *     resets if the image flies off-screen
 *   - touch: single-finger pan (pinch/zoom is v2; we keep
 *     touch-action: none so the page doesn't scroll under the user)
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

const MIN_SCALE = 0.25;
const MAX_SCALE = 4;

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
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

  // Reset transforms when the row OR the active image (within a batch
  // row) changes.
  useEffect(() => {
    setScale(1);
    setTranslate({ x: 0, y: 0 });
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

  // Zoom-at-cursor math.
  const zoomAt = useCallback(
    (factor, cursorX, cursorY) => {
      const stage = stageRef.current?.getBoundingClientRect();
      if (!stage) return;
      const cx = (cursorX ?? stage.left + stage.width / 2) - stage.left - stage.width / 2;
      const cy = (cursorY ?? stage.top + stage.height / 2) - stage.top - stage.height / 2;
      setScale((prev) => {
        const next = clamp(prev * factor, MIN_SCALE, MAX_SCALE);
        if (next === prev) return prev;
        const k = next / prev;
        setTranslate((tr) => ({
          x: cx - (cx - tr.x) * k,
          y: cy - (cy - tr.y) * k,
        }));
        return next;
      });
    },
    []
  );

  const fit = useCallback(() => {
    setAnimateImg(true);
    setScale(1);
    setTranslate({ x: 0, y: 0 });
  }, []);

  const oneToOne = useCallback(() => {
    const el = imgRef.current;
    const nat = img?.width;
    if (!el || !nat) return;
    const rect = el.getBoundingClientRect();
    if (!rect.width) return;
    const target = nat / rect.width;
    setAnimateImg(true);
    setScale(clamp(target, MIN_SCALE, MAX_SCALE));
    setTranslate({ x: 0, y: 0 });
  }, [img?.width]);

  // Wheel — cursor-anchored zoom.
  useEffect(() => {
    if (!open) return undefined;
    const stage = stageRef.current;
    if (!stage) return undefined;
    const handler = (e) => {
      e.preventDefault();
      setAnimateImg(false);
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      zoomAt(factor, e.clientX, e.clientY);
    };
    stage.addEventListener("wheel", handler, { passive: false });
    return () => stage.removeEventListener("wheel", handler);
  }, [open, zoomAt]);

  // Pointer-based pan (mouse + touch unified).
  const dragStateRef = useRef({ active: false, startX: 0, startY: 0, baseX: 0, baseY: 0 });
  const onPointerDown = (e) => {
    if (scale <= 1) return;
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    dragStateRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      baseX: translate.x,
      baseY: translate.y,
    };
    setAnimateImg(false);
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e) => {
    const ds = dragStateRef.current;
    if (!ds.active) return;
    const dx = e.clientX - ds.startX;
    const dy = e.clientY - ds.startY;
    setTranslate({ x: ds.baseX + dx, y: ds.baseY + dy });
  };
  const onPointerUp = (e) => {
    if (dragStateRef.current.active) {
      dragStateRef.current.active = false;
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    }
  };
  const onDoubleClick = (e) => {
    setAnimateImg(true);
    if (scale === 1) {
      zoomAt(2, e.clientX, e.clientY);
    } else {
      setScale(1);
      setTranslate({ x: 0, y: 0 });
    }
  };

  // Close handler with fade-out.
  const handleClose = useCallback(() => {
    setClosing(true);
    const t = setTimeout(() => {
      setClosing(false);
      onClose?.();
    }, 180);
    return () => clearTimeout(t);
  }, [onClose]);

  // Toggle star helper.
  const toggleStar = useCallback(() => {
    if (!row || !img) return;
    void archiveStore.toggleStar(row.hash_id, img.order);
  }, [row, img]);

  // Download helper.
  const downloadCurrent = useCallback(() => {
    if (!row || !img) return;
    void downloadImageFile(
      imageOriginalUrl(row.hash_id, img.order),
      `${row.hash_id}_${String(img.order).padStart(2, "0")}.${img.format || "bin"}`
    );
    flash("download started");
  }, [row, img, flash]);

  // Copy prompt helper.
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
        zoomAt(1.25);
      } else if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        setAnimateImg(true);
        zoomAt(1 / 1.25);
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
  }, [open, handleClose, onPrev, onNext, zoomAt, fit, oneToOne, toggleStar, downloadCurrent]);

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

  const node = (
    <div
      className={`lb-root ${closing ? "lb-closing" : ""}`.trim()}
      role="dialog"
      aria-modal="true"
      aria-label="Image preview"
      data-testid="lightbox"
    >
      {/* TOP NAV ----------------------------------------------------- */}
      <div className="lb-nav" data-testid="lightbox-nav">
        <span className="lb-nav-tag">JOB</span>
        <span className="lb-nav-id">#{row.seq_no ?? "—"}</span>
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
          <span className="lb-page-counter">
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
        style={{ touchAction: "none" }}
      >
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
            data-testid="lightbox-img"
            data-original={originalSrc ? "1" : "0"}
            style={{
              aspectRatio: naturalRatio,
              transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
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
          onClick={() => { setAnimateImg(true); zoomAt(1 / 1.25); }}
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
          onClick={() => { setAnimateImg(true); zoomAt(1.25); }}
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
          data-testid="lightbox-zoom-1to1"
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
    </div>
  );

  return createPortal(node, document.body);
}
