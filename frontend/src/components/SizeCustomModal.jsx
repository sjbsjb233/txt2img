import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

// Size picker — a recommended catalog grouped by aspect ratio plus a
// manual W×H input that mirrors the OpenAI gpt-image-2 v2 contract.
//
// Backend is the source of truth (``app/adapters/openai_v1.py:validate_custom_size``);
// this validator is a strict mirror so the user gets immediate feedback
// instead of a 422 round-trip.
//
// The 2026-05 redesign keeps the public API (props, exports, testids)
// identical to the previous version so it remains a drop-in replacement
// for ``CreatePage``. Visuals follow the Claude Design hand-off bundle
// ``SizeCustomModal.html`` from ``stable-nano-2``.

const SIZE_LIMITS = Object.freeze({
  multiple: 16,
  maxEdge: 3840,
  minPixels: 655_360,
  maxPixels: 8_294_400,
  maxRatio: 3,
  experimentalEdge: 2560,
  experimentalShort: 1440,
});

export function isValidSize(width, height) {
  const w = Number(width);
  const h = Number(height);
  if (!Number.isInteger(w) || !Number.isInteger(h)) {
    return { ok: false, reason: "Width and height must be whole numbers." };
  }
  if (w <= 0 || h <= 0) {
    return { ok: false, reason: "Width and height must be positive." };
  }
  if (w % SIZE_LIMITS.multiple || h % SIZE_LIMITS.multiple) {
    return {
      ok: false,
      reason: `Width and height must each be multiples of ${SIZE_LIMITS.multiple}.`,
    };
  }
  if (Math.max(w, h) > SIZE_LIMITS.maxEdge) {
    return {
      ok: false,
      reason: `Longest edge must be ≤ ${SIZE_LIMITS.maxEdge}px.`,
    };
  }
  const px = w * h;
  if (px < SIZE_LIMITS.minPixels) {
    return {
      ok: false,
      reason: `Total pixels must be ≥ ${SIZE_LIMITS.minPixels.toLocaleString()}.`,
    };
  }
  if (px > SIZE_LIMITS.maxPixels) {
    return {
      ok: false,
      reason: `Total pixels must be ≤ ${SIZE_LIMITS.maxPixels.toLocaleString()}.`,
    };
  }
  const ratio = Math.max(w, h) / Math.min(w, h);
  if (ratio > SIZE_LIMITS.maxRatio + 1e-9) {
    return {
      ok: false,
      reason: `Aspect ratio ${ratio.toFixed(2)}:1 exceeds the ${SIZE_LIMITS.maxRatio}:1 cap.`,
    };
  }
  const experimental =
    Math.max(w, h) > SIZE_LIMITS.experimentalEdge ||
    Math.min(w, h) > SIZE_LIMITS.experimentalShort;
  return { ok: true, reason: "", experimental };
}

const CATALOG = [
  {
    title: "16:9 / 9:16",
    note: "video, banners, phone wallpapers",
    items: [
      { label: "HD landscape", w: 1280, h: 720 },
      { label: "HD portrait", w: 720, h: 1280 },
      { label: "FHD landscape*", w: 1920, h: 1088, hint: "*1080 isn't 16-multiple; 1088 is" },
      { label: "FHD portrait*", w: 1088, h: 1920, hint: "*1080 isn't 16-multiple; 1088 is" },
      { label: "2K landscape", w: 2560, h: 1440 },
      { label: "2K portrait", w: 1440, h: 2560 },
    ],
  },
  {
    title: "3:2 / 2:3",
    note: "DSLR-style",
    items: [
      { label: "DSLR landscape", w: 2400, h: 1600 },
      { label: "DSLR portrait", w: 1600, h: 2400 },
    ],
  },
  {
    title: "4:3 / 3:4",
    note: "classic monitor / photo print",
    items: [
      { label: "Landscape — small", w: 1280, h: 960 },
      { label: "Portrait — small", w: 960, h: 1280 },
      { label: "Landscape — medium", w: 1600, h: 1200 },
      { label: "Portrait — medium", w: 1200, h: 1600 },
    ],
  },
  {
    title: "4:5 / 5:4",
    note: "Instagram, posters",
    items: [
      { label: "IG portrait", w: 1024, h: 1280 },
      { label: "IG portrait — large", w: 1280, h: 1600 },
      { label: "5:4 landscape", w: 1280, h: 1024 },
    ],
  },
  {
    title: "21:9 cinema",
    note: "ultrawide, cinematic",
    items: [
      { label: "Cinema — small", w: 1680, h: 720 },
      { label: "Cinema — medium", w: 2240, h: 960 },
      { label: "Cinema — large", w: 3360, h: 1440 },
    ],
  },
  {
    title: "3:1 banner",
    note: "extreme banners — some relays clamp at 2:1",
    items: [
      { label: "Banner — small", w: 1536, h: 512 },
      { label: "Banner — medium", w: 2304, h: 768 },
      { label: "Banner — max", w: 3840, h: 1280 },
    ],
  },
  {
    title: "Square / 4K",
    note: "experimental, may take longer to render",
    items: [
      { label: "Square — large", w: 1280, h: 1280 },
      { label: "Square — 2K", w: 2048, h: 2048 },
      { label: "4K UHD", w: 3840, h: 2160 },
    ],
  },
];

// Largest preset edge used to scale the per-card ratio glyph. Computed
// once at module load so each card keeps its true relative aspect.
const MAX_PRESET_EDGE = CATALOG.reduce((acc, group) => {
  group.items.forEach((it) => {
    if (it.w > acc) acc = it.w;
    if (it.h > acc) acc = it.h;
  });
  return acc;
}, 0);
const RATIO_STAGE_PX = 56;

function ratioBoxStyle(w, h) {
  const scale = RATIO_STAGE_PX / MAX_PRESET_EDGE;
  const dispW = Math.max(8, Math.round(w * scale));
  const dispH = Math.max(8, Math.round(h * scale));
  return { width: `${dispW}px`, height: `${dispH}px` };
}

function cleanRatio(w, h) {
  const a = Math.max(1, Math.round(w));
  const b = Math.max(1, Math.round(h));
  const gcd = (x, y) => (y ? gcd(y, x % y) : x);
  const g = gcd(a, b);
  const rw = a / g;
  const rh = b / g;
  if (rw < 100 && rh < 100) return `${rw}:${rh}`;
  return `${(w / h).toFixed(2)}:1`;
}

function pickPresetLabel(w, h) {
  for (const group of CATALOG) {
    for (const it of group.items) {
      if (it.w === w && it.h === h) return it.label;
    }
  }
  return "Custom";
}

// Default preview shown when the modal opens without an ``initialValue``.
// Computing the label via ``pickPresetLabel`` keeps the header in sync if
// the matching preset is ever renamed in ``CATALOG``.
const DEFAULT_PREVIEW = Object.freeze({
  w: 2560,
  h: 1440,
  label: pickPresetLabel(2560, 1440),
});

// Pre-format constants for the constraint strip so the UI mirrors
// ``SIZE_LIMITS`` exactly. Helpers, not literals — if a limit moves the
// strip moves with it.
function _fmtThousands(n) {
  // 655360 → "655 360" (NBSP-style narrow gap, matches the original copy).
  return n.toLocaleString("en-US").replace(/,/g, " ");
}
function _fmtMillionsCompact(n) {
  // 8294400 → "8.29M".
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export default function SizeCustomModal({ open, initialValue, onSelect, onClose }) {
  const [w, setW] = useState("");
  const [h, setH] = useState("");
  const [touched, setTouched] = useState(false);
  // Visual preview state — separate from the manual inputs so a preset
  // click instantly refreshes the right pane even before the controlled
  // state pushes back to the parent on apply.
  const [preview, setPreview] = useState(DEFAULT_PREVIEW);
  const firstButtonRef = useRef(null);
  const wInputRef = useRef(null);
  const stageRef = useRef(null);
  const [stageSize, setStageSize] = useState({ w: 280, h: 240 });

  useEffect(() => {
    if (!open) {
      setTouched(false);
      return;
    }
    if (typeof initialValue === "string" && initialValue.match(/^\d+x\d+$/i)) {
      const [iw, ih] = initialValue.split(/x/i).map((s) => parseInt(s, 10));
      setW(String(iw));
      setH(String(ih));
      if (Number.isFinite(iw) && Number.isFinite(ih)) {
        setPreview({ w: iw, h: ih, label: pickPresetLabel(iw, ih) });
      }
    } else {
      setW("");
      setH("");
      setPreview(DEFAULT_PREVIEW);
    }
    setTimeout(() => firstButtonRef.current?.focus(), 50);
  }, [open, initialValue]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Track the stage size so the preview frame can scale proportionally
  // without overflowing on smaller viewports.
  useLayoutEffect(() => {
    if (!open) return;
    const node = stageRef.current;
    if (!node) return;
    const update = () => {
      const rect = node.getBoundingClientRect();
      setStageSize({ w: Math.max(40, rect.width), h: Math.max(40, rect.height) });
    };
    update();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    ro?.observe(node);
    window.addEventListener("resize", update);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [open]);

  const manualCheck = useMemo(() => {
    const wn = Number(w);
    const hn = Number(h);
    if (!w || !h) {
      return { ok: false, reason: "", empty: true };
    }
    if (!Number.isFinite(wn) || !Number.isFinite(hn)) {
      return { ok: false, reason: "Width and height must be numbers.", empty: false };
    }
    return { ...isValidSize(wn, hn), empty: false };
  }, [w, h]);

  if (!open) return null;

  // Picking a preset only stages the choice in the preview pane and
  // mirrors it into the manual W/H inputs — it does NOT commit. The
  // user must press the footer ``Use this size →`` (or hit Enter on a
  // manual input) to actually fire ``onSelect`` and close. This matches
  // the "preview first, apply second" intent of the redesigned modal:
  // the right pane is a real preview, not a side-effect of picking.
  const pickFromCatalog = (item) => {
    setW(String(item.w));
    setH(String(item.h));
    setPreview({ w: item.w, h: item.h, label: item.label });
    setTouched(false);
  };

  // Single commit path used by both the Apply button and the Enter key
  // on the manual inputs. Source of truth is ``preview``, which always
  // tracks either the last-clicked preset or the current manual values.
  const applyPreview = () => {
    setTouched(true);
    if (!previewValid.ok) return;
    onSelect?.(`${preview.w}x${preview.h}`);
  };

  const onWChange = (e) => {
    const next = e.target.value.replace(/[^\d]/g, "");
    setW(next);
    setTouched(true);
    const wn = parseInt(next, 10);
    const hn = parseInt(h, 10);
    if (Number.isFinite(wn) && wn > 0 && Number.isFinite(hn) && hn > 0) {
      setPreview({ w: wn, h: hn, label: pickPresetLabel(wn, hn) });
    }
  };
  const onHChange = (e) => {
    const next = e.target.value.replace(/[^\d]/g, "");
    setH(next);
    setTouched(true);
    const wn = parseInt(w, 10);
    const hn = parseInt(next, 10);
    if (Number.isFinite(wn) && wn > 0 && Number.isFinite(hn) && hn > 0) {
      setPreview({ w: wn, h: hn, label: pickPresetLabel(wn, hn) });
    }
  };
  const onManualKey = (e) => {
    if (e.key === "Enter") applyPreview();
  };

  // Frame size inside the live-preview stage. Honors the actual aspect
  // ratio of ``preview`` and uses ~92% of the stage on the constraining
  // axis (matching the design's max-width/max-height: 92% rule).
  const frame = (() => {
    const { w: pw, h: ph } = preview;
    if (!pw || !ph) return { width: 0, height: 0 };
    const stageW = stageSize.w * 0.92;
    const stageH = stageSize.h * 0.92;
    const ratio = pw / ph;
    let fw = stageW;
    let fh = fw / ratio;
    if (fh > stageH) {
      fh = stageH;
      fw = fh * ratio;
    }
    return { width: Math.max(20, fw), height: Math.max(20, fh) };
  })();

  const previewValid = isValidSize(preview.w, preview.h);
  const ratioStr = cleanRatio(preview.w, preview.h);
  const pixelsMP = ((preview.w * preview.h) / 1_000_000).toFixed(2);
  const currentSizeStr = `${preview.w}x${preview.h}`;

  const previewStatusOk = previewValid.ok;
  // Apply tracks the *preview* — not just the manual inputs — so a
  // preset click can be committed even though the user has not typed
  // anything in the manual fields.
  const applyDisabled = !previewValid.ok;

  return (
    <div
      data-testid="size-custom-modal"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 90,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#19171488",
        backdropFilter: "blur(2px)",
        animation: "scsFade 160ms ease-out",
        padding: 24,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <style>{`
        @keyframes scsFade { from { opacity: 0; } to { opacity: 1; } }
        @keyframes scsPop { from { opacity: 0; transform: translateY(8px) scale(.985); } to { opacity: 1; transform: none; } }

        .scs-modal {
          width: 920px;
          max-width: 100%;
          max-height: calc(100vh - 48px);
          background: var(--paper);
          border: 1.5px solid var(--ink);
          box-shadow: 10px 10px 0 var(--ink);
          display: flex;
          flex-direction: column;
          animation: scsPop 220ms cubic-bezier(.2,.85,.2,1);
        }

        .scs-head {
          display: grid;
          grid-template-columns: 1fr auto;
          align-items: center;
          background: var(--ink);
          color: var(--paper);
          padding: 14px 22px;
          border-bottom: 1.5px solid var(--ink);
          gap: 16px;
        }
        .scs-crumbs {
          display: flex;
          align-items: center;
          gap: 10px;
          font-family: var(--font-mono);
          font-size: 10px;
          letter-spacing: 0.18em;
          text-transform: uppercase;
          color: var(--paper);
          flex-wrap: wrap;
        }
        .scs-crumbs .scs-dot { color: var(--banana); }
        .scs-crumbs .scs-now {
          color: var(--banana);
          border: 1px solid var(--banana);
          padding: 2px 8px;
        }
        .scs-head h2 {
          grid-column: 1 / -1;
          margin: 8px 0 0;
          font-family: var(--font-display);
          font-weight: 500;
          font-size: 26px;
          letter-spacing: -0.01em;
          color: var(--paper);
          line-height: 1.05;
        }
        .scs-head h2 em {
          color: var(--banana);
          font-style: italic;
          font-weight: 700;
        }
        .scs-x-btn {
          align-self: start;
          background: transparent;
          border: 1px solid var(--paper);
          color: var(--paper);
          width: 28px; height: 28px;
          display: inline-flex; align-items: center; justify-content: center;
          cursor: pointer;
          font-family: var(--font-mono);
          font-size: 12px;
          padding: 0;
        }
        .scs-x-btn:hover {
          background: var(--banana);
          color: var(--ink);
          border-color: var(--banana);
        }

        .scs-strip {
          display: grid;
          grid-template-columns: repeat(5, 1fr);
          border-bottom: 1.5px solid var(--ink);
          background: var(--paper-2);
        }
        .scs-strip .cell {
          padding: 10px 14px;
          border-right: 1px solid var(--rule-2);
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .scs-strip .cell:last-child { border-right: none; }
        .scs-strip .k {
          font-family: var(--font-mono);
          font-size: 9px;
          letter-spacing: 0.16em;
          text-transform: uppercase;
          color: var(--ink-3);
        }
        .scs-strip .v {
          font-family: var(--font-mono);
          font-weight: 700;
          font-size: 13px;
          color: var(--ink);
        }
        .scs-strip .v small {
          color: var(--ink-3);
          font-weight: 400;
          margin-left: 4px;
        }

        .scs-body {
          display: grid;
          grid-template-columns: 1fr 320px;
          flex: 1;
          min-height: 0;
        }
        .scs-catalog {
          overflow-y: auto;
          padding: 18px 22px 22px;
          border-right: 1.5px solid var(--ink);
          min-width: 0;
        }
        .scs-preview {
          background: var(--paper-2);
          display: flex;
          flex-direction: column;
          overflow: hidden;
          min-width: 0;
        }

        .scs-step {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-bottom: 14px;
        }
        .scs-step .num {
          width: 22px; height: 22px;
          border: 1px solid var(--ink);
          background: var(--banana);
          display: inline-flex; align-items: center; justify-content: center;
          font-family: var(--font-mono);
          font-size: 11px;
          font-weight: 700;
          color: var(--ink);
        }
        .scs-step .t {
          font-family: var(--font-display);
          font-size: 18px;
          font-weight: 500;
          letter-spacing: -0.005em;
          color: var(--ink);
        }
        .scs-step .h {
          margin-left: auto;
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--ink-3);
          letter-spacing: 0.1em;
          text-transform: uppercase;
        }

        .scs-group { margin-bottom: 18px; }
        .scs-group-head {
          display: grid;
          grid-template-columns: 86px 1fr;
          align-items: center;
          gap: 14px;
          padding: 6px 0 8px;
          border-top: 1px solid var(--rule-2);
        }
        .scs-group:first-child .scs-group-head {
          border-top: none;
          padding-top: 2px;
        }
        .scs-group-tag {
          font-family: var(--font-mono);
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.06em;
          color: var(--ink);
          background: var(--paper-soft, #fffdf7);
          border: 1px solid var(--ink);
          padding: 4px 8px;
          text-align: center;
        }
        .scs-group-note {
          font-size: 11px;
          color: var(--ink-3);
          line-height: 1.4;
        }

        .scs-preset-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
          margin-top: 8px;
        }
        @media (max-width: 720px) {
          .scs-preset-grid { grid-template-columns: repeat(2, 1fr); }
          .scs-body { grid-template-columns: 1fr; }
          .scs-catalog { border-right: none; border-bottom: 1.5px solid var(--ink); }
        }

        .scs-preset {
          position: relative;
          border: 1px solid var(--ink);
          background: var(--paper-soft, #fffdf7);
          padding: 10px;
          cursor: pointer;
          text-align: left;
          font-family: inherit;
          color: var(--ink);
          display: flex;
          flex-direction: column;
          gap: 8px;
          transition: transform 80ms ease, background 80ms ease, box-shadow 80ms ease;
        }
        .scs-preset:hover {
          background: var(--paper-2);
          transform: translate(-1px, -1px);
          box-shadow: 2px 2px 0 var(--ink);
        }
        .scs-preset[data-on="true"] {
          background: var(--ink);
          color: var(--paper);
        }
        .scs-preset[data-on="true"] .scs-ratio-box {
          background: var(--banana);
          border-color: var(--banana);
        }
        .scs-preset[data-on="true"] .scs-px-info {
          color: var(--paper-2);
        }
        .scs-preset[data-on="true"] .scs-exp-tag {
          background: var(--banana);
          color: var(--ink);
          border-color: var(--banana);
        }
        .scs-preset:focus-visible {
          outline: 2px solid var(--banana);
          outline-offset: 2px;
        }

        .scs-ratio-stage {
          height: 64px;
          background:
            linear-gradient(to right, transparent 0, transparent 49.5%, #19171420 49.5%, #19171420 50.5%, transparent 50.5%),
            linear-gradient(to bottom, transparent 0, transparent 49.5%, #19171420 49.5%, #19171420 50.5%, transparent 50.5%),
            repeating-linear-gradient(45deg, transparent 0 6px, #19171410 6px 7px);
          border: 1px dashed var(--rule-2);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 4px;
        }
        .scs-preset[data-on="true"] .scs-ratio-stage {
          background:
            linear-gradient(to right, transparent 0, transparent 49.5%, #f4efe630 49.5%, #f4efe630 50.5%, transparent 50.5%),
            linear-gradient(to bottom, transparent 0, transparent 49.5%, #f4efe630 49.5%, #f4efe630 50.5%, transparent 50.5%),
            repeating-linear-gradient(45deg, transparent 0 6px, #f4efe610 6px 7px);
          border-color: #f4efe630;
        }
        .scs-ratio-box {
          background: var(--ink);
          border: 1px solid var(--ink);
        }

        .scs-label-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 6px;
        }
        .scs-label {
          font-size: 12px;
          font-weight: 700;
          letter-spacing: -0.005em;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .scs-px-info {
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--ink-3);
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .scs-exp-tag {
          display: inline-block;
          padding: 1px 5px;
          border: 1px solid var(--ink);
          background: var(--banana-soft);
          font-family: var(--font-mono);
          font-size: 8px;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--ink);
        }

        .scs-pv-head {
          padding: 14px 18px 8px;
          border-bottom: 1px solid var(--rule-2);
        }
        .scs-pv-stage {
          flex: 1;
          min-height: 240px;
          position: relative;
          display: flex;
          align-items: center;
          justify-content: center;
          background:
            repeating-linear-gradient(45deg, transparent 0 12px, #19171408 12px 13px),
            var(--paper-3);
          overflow: hidden;
          border-bottom: 1px solid var(--rule-2);
        }
        .scs-pv-frame {
          background: var(--banana);
          border: 1.5px solid var(--ink);
          box-shadow: 4px 4px 0 var(--ink);
          position: relative;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: width 220ms cubic-bezier(.2,.85,.2,1), height 220ms cubic-bezier(.2,.85,.2,1);
        }
        .scs-pv-frame .corner {
          position: absolute;
          width: 14px; height: 14px;
          border: 1.5px solid var(--ink);
          background: var(--paper);
        }
        .scs-pv-frame .corner.tl { top: -7px; left: -7px; }
        .scs-pv-frame .corner.tr { top: -7px; right: -7px; }
        .scs-pv-frame .corner.bl { bottom: -7px; left: -7px; }
        .scs-pv-frame .corner.br { bottom: -7px; right: -7px; }
        .scs-pv-frame .ratio-num {
          font-family: var(--font-display);
          font-weight: 700;
          font-size: 32px;
          color: var(--ink);
          letter-spacing: -0.02em;
        }
        .scs-pv-dim-w {
          position: absolute;
          bottom: 8px;
          left: 50%;
          transform: translateX(-50%);
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--ink-2);
          letter-spacing: 0.1em;
          background: var(--paper);
          padding: 2px 6px;
          border: 1px solid var(--ink);
          white-space: nowrap;
        }
        .scs-pv-dim-h {
          position: absolute;
          right: 8px;
          top: 50%;
          transform: translateY(-50%) rotate(90deg);
          transform-origin: center;
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--ink-2);
          letter-spacing: 0.1em;
          background: var(--paper);
          padding: 2px 6px;
          border: 1px solid var(--ink);
          white-space: nowrap;
        }

        .scs-pv-stats {
          padding: 12px 18px;
          border-bottom: 1px solid var(--rule-2);
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 8px 14px;
        }
        .scs-pv-stats .row {
          display: flex;
          justify-content: space-between;
          font-family: var(--font-mono);
          font-size: 11px;
        }
        .scs-pv-stats .row .k { color: var(--ink-3); }
        .scs-pv-stats .row .v { color: var(--ink); font-weight: 700; }

        .scs-manual {
          padding: 14px 18px;
          background: var(--paper);
          border-top: 1.5px solid var(--ink);
        }
        .scs-manual .ml {
          display: flex; align-items: center; gap: 8px;
          margin-bottom: 8px;
        }
        .scs-manual .ml .num {
          width: 20px; height: 20px;
          border: 1px solid var(--ink);
          background: var(--banana);
          display: inline-flex; align-items: center; justify-content: center;
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 700;
          color: var(--ink);
        }
        .scs-manual .ml .t {
          font-family: var(--font-display);
          font-size: 15px;
          font-weight: 600;
          color: var(--ink);
        }
        .scs-wh-row {
          display: grid;
          grid-template-columns: 1fr 14px 1fr;
          gap: 6px;
          align-items: stretch;
        }
        .scs-wh {
          border: 1px solid var(--ink);
          background: var(--paper-soft, #fffdf7);
          display: flex;
          flex-direction: column;
        }
        .scs-wh:focus-within {
          outline: 2px solid var(--banana);
          outline-offset: -2px;
        }
        .scs-wh label {
          font-family: var(--font-mono);
          font-size: 9px;
          color: var(--ink-3);
          letter-spacing: 0.14em;
          text-transform: uppercase;
          padding: 4px 8px 0;
        }
        .scs-wh input {
          width: 100%;
          border: none;
          outline: none;
          background: transparent;
          font-family: var(--font-mono);
          font-size: 18px;
          font-weight: 700;
          padding: 0 8px 6px;
          color: var(--ink);
        }
        .scs-wh-x {
          align-self: center;
          text-align: center;
          font-family: var(--font-display);
          font-style: italic;
          color: var(--ink-3);
          font-size: 18px;
        }
        .scs-wh-tip {
          margin-top: 8px;
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--ink-3);
          line-height: 1.5;
          min-height: 14px;
        }
        .scs-wh-tip[data-state="ok"] { color: var(--ok); }
        .scs-wh-tip[data-state="bad"] { color: var(--bad); }
        .scs-wh-tip[data-state="exp"] { color: var(--warn); }

        .scs-foot {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 14px 22px;
          background: var(--paper);
          border-top: 1.5px solid var(--ink);
        }
        .scs-foot .lhs {
          font-family: var(--font-mono);
          font-size: 11px;
          color: var(--ink-3);
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }
        .scs-foot .rhs { display: flex; gap: 8px; }
        .scs-btn {
          height: 38px;
          padding: 0 18px;
          border: 1px solid var(--ink);
          background: var(--paper-soft, #fffdf7);
          font-family: var(--font-sans);
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          color: var(--ink);
        }
        .scs-btn:hover { background: var(--paper-2); }
        .scs-btn.primary {
          background: var(--ink);
          color: var(--banana);
        }
        .scs-btn.primary:hover { background: #000; }
        .scs-btn.primary[disabled] {
          opacity: 0.4;
          cursor: not-allowed;
        }
        .scs-btn.shadowed { box-shadow: 3px 3px 0 var(--ink); }
        .scs-btn.shadowed:hover {
          box-shadow: 2px 2px 0 var(--ink);
          transform: translate(1px,1px);
        }
        .scs-kbd {
          display: inline-flex;
          align-items: center;
          height: 18px;
          padding: 0 5px;
          border: 1px solid var(--ink);
          background: var(--paper-soft, #fffdf7);
          font-family: var(--font-mono);
          font-size: 10px;
          box-shadow: 0 2px 0 var(--ink);
          color: var(--ink);
        }
      `}</style>

      <div className="scs-modal" role="dialog" aria-label="Custom size picker" aria-modal="true">
        {/* HEADER */}
        <div className="scs-head">
          <div className="scs-crumbs">
            <span>Generate</span>
            <span className="scs-dot">/</span>
            <span>Image size</span>
            <span className="scs-dot">/</span>
            <span className="scs-now">Custom · gpt-image-2</span>
          </div>
          <button
            data-testid="size-custom-close"
            className="scs-x-btn"
            onClick={onClose}
            aria-label="Close size picker"
            type="button"
          >
            ✕
          </button>
          <h2>
            Pick a size that matches your <em>canvas</em>.
          </h2>
        </div>

        {/* CONSTRAINT STRIP — every value below is derived from
            SIZE_LIMITS so the UI cannot drift from the validator. */}
        <div className="scs-strip" aria-label="Backend constraints">
          <div className="cell">
            <span className="k">Multiple</span>
            <span className="v">
              {SIZE_LIMITS.multiple}
              <small>px sides</small>
            </span>
          </div>
          <div className="cell">
            <span className="k">Longest edge</span>
            <span className="v">
              ≤ {SIZE_LIMITS.maxEdge}
              <small>px</small>
            </span>
          </div>
          <div className="cell">
            <span className="k">Total pixels</span>
            <span className="v">
              {_fmtThousands(SIZE_LIMITS.minPixels)} –{" "}
              {_fmtMillionsCompact(SIZE_LIMITS.maxPixels)}
            </span>
          </div>
          <div className="cell">
            <span className="k">Aspect ratio</span>
            <span className="v">≤ {SIZE_LIMITS.maxRatio} : 1</span>
          </div>
          <div className="cell">
            <span className="k">Experimental</span>
            <span className="v" style={{ color: "var(--warn)" }}>
              &gt;{SIZE_LIMITS.experimentalEdge} long / &gt;
              {SIZE_LIMITS.experimentalShort} short
            </span>
          </div>
        </div>

        {/* BODY */}
        <div className="scs-body">
          {/* LEFT — catalog */}
          <div className="scs-catalog">
            <div className="scs-step">
              <span className="num">1</span>
              <span className="t">Pick a curated preset</span>
              <span className="h">All sizes pass every rule</span>
            </div>

            {CATALOG.map((group, gi) => (
              <div key={group.title} className="scs-group">
                <div className="scs-group-head">
                  <div className="scs-group-tag">{group.title}</div>
                  <div className="scs-group-note">{group.note}</div>
                </div>
                <div className="scs-preset-grid">
                  {group.items.map((item, idx) => {
                    const v = isValidSize(item.w, item.h);
                    const sizeStr = `${item.w}x${item.h}`;
                    const matches =
                      preview.w === item.w && preview.h === item.h;
                    const px = ((item.w * item.h) / 1_000_000).toFixed(2);
                    return (
                      <button
                        key={item.label}
                        ref={gi === 0 && idx === 0 ? firstButtonRef : undefined}
                        type="button"
                        className="scs-preset"
                        data-on={matches ? "true" : "false"}
                        data-testid={`size-preset-${sizeStr}`}
                        onClick={() => pickFromCatalog(item)}
                        title={item.hint || `${item.w}×${item.h}`}
                      >
                        <div className="scs-ratio-stage">
                          <div
                            className="scs-ratio-box"
                            style={ratioBoxStyle(item.w, item.h)}
                          />
                        </div>
                        <div className="scs-label-row">
                          <span className="scs-label">{item.label}</span>
                          {v.experimental ? (
                            <span className="scs-exp-tag">exp</span>
                          ) : null}
                        </div>
                        <div className="scs-px-info">
                          <span>
                            {item.w}×{item.h}
                          </span>
                          <span>{px} MP</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* RIGHT — live preview */}
          <div className="scs-preview">
            <div className="scs-pv-head">
              <div
                className="mono caps"
                style={{ fontSize: 10, color: "var(--ink-3)" }}
              >
                Live preview
              </div>
              <div
                className="display"
                data-testid="size-custom-preview-title"
                style={{
                  fontSize: 20,
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  marginTop: 2,
                  lineHeight: 1.15,
                }}
              >
                {preview.label}
              </div>
            </div>

            <div className="scs-pv-stage" ref={stageRef}>
              <div
                className="scs-pv-frame"
                style={{ width: frame.width, height: frame.height }}
              >
                <span className="corner tl" />
                <span className="corner tr" />
                <span className="corner bl" />
                <span className="corner br" />
                <span className="ratio-num">{ratioStr}</span>
                <span className="scs-pv-dim-w">{preview.w} px</span>
                <span className="scs-pv-dim-h">{preview.h} px</span>
              </div>
            </div>

            <div className="scs-pv-stats">
              <div className="row">
                <span className="k">Width</span>
                <span className="v" data-testid="size-custom-stat-w">{preview.w}</span>
              </div>
              <div className="row">
                <span className="k">Height</span>
                <span className="v" data-testid="size-custom-stat-h">{preview.h}</span>
              </div>
              <div className="row">
                <span className="k">Pixels</span>
                <span className="v">{pixelsMP} MP</span>
              </div>
              <div className="row">
                <span className="k">Ratio</span>
                <span className="v">{ratioStr}</span>
              </div>
              <div className="row">
                <span className="k">Zone</span>
                <span
                  className="v"
                  data-testid="size-custom-zone"
                  style={{
                    color: !previewValid.ok
                      ? "var(--ink-3)"
                      : previewValid.experimental
                      ? "var(--warn)"
                      : "var(--ok)",
                  }}
                >
                  {!previewValid.ok
                    ? "—"
                    : previewValid.experimental
                    ? "Experimental"
                    : "Stable"}
                </span>
              </div>
              <div className="row">
                <span className="k">Status</span>
                <span
                  className="v"
                  data-testid="size-custom-status"
                  style={{
                    color: previewStatusOk ? "var(--ok)" : "var(--bad)",
                  }}
                >
                  {previewStatusOk ? "✓ Valid" : "✕ Invalid"}
                </span>
              </div>
            </div>

            {/* MANUAL */}
            <div className="scs-manual">
              <div className="ml">
                <span className="num">2</span>
                <span className="t">Or type your own</span>
              </div>
              <div className="scs-wh-row">
                <div className="scs-wh">
                  <label htmlFor="scs-w">Width</label>
                  <input
                    id="scs-w"
                    data-testid="size-custom-width"
                    ref={wInputRef}
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={w}
                    onChange={onWChange}
                    onKeyDown={onManualKey}
                    placeholder="1920"
                    aria-label="Custom width"
                  />
                </div>
                <span className="scs-wh-x">×</span>
                <div className="scs-wh">
                  <label htmlFor="scs-h">Height</label>
                  <input
                    id="scs-h"
                    data-testid="size-custom-height"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={h}
                    onChange={onHChange}
                    onKeyDown={onManualKey}
                    placeholder="1088"
                    aria-label="Custom height"
                  />
                </div>
              </div>
              <div
                data-testid="size-custom-validation"
                className="scs-wh-tip"
                data-state={
                  manualCheck.empty
                    ? ""
                    : manualCheck.ok
                    ? manualCheck.experimental
                      ? "exp"
                      : "ok"
                    : "bad"
                }
              >
                {manualCheck.empty
                  ? "Both sides must be multiples of 16."
                  : manualCheck.ok
                  ? `${manualCheck.experimental ? "△" : "✓"} ${Number(w)}×${Number(h)} · ${(
                      (Number(w) * Number(h)) / 1_000_000
                    ).toFixed(2)} MP · ${
                      manualCheck.experimental ? "experimental zone" : "stable zone"
                    } — looks good.`
                  : touched
                  ? `✕ ${manualCheck.reason}`
                  : ""}
              </div>
            </div>
          </div>
        </div>

        {/* FOOTER */}
        <div className="scs-foot">
          <div className="lhs">
            <span>
              <span className="scs-kbd">Esc</span> close
            </span>
            <span style={{ color: "var(--ink-4)" }}>·</span>
            <span>
              <span className="scs-kbd">↵</span> apply manual
            </span>
            <span style={{ color: "var(--ink-4)" }}>·</span>
            <span data-testid="size-custom-current-size">{currentSizeStr}</span>
          </div>
          <div className="rhs">
            <button
              type="button"
              className="scs-btn"
              data-testid="size-custom-cancel"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="scs-btn primary shadowed"
              data-testid="size-custom-apply"
              onClick={applyPreview}
              disabled={applyDisabled}
            >
              Use this size →
              <span
                className="scs-kbd"
                style={{ background: "var(--banana)", color: "var(--ink)" }}
              >
                ↵
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
