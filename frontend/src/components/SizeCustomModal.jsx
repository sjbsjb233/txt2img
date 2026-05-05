import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";

// Size picker — a recommended catalog grouped by aspect ratio plus a
// manual W×H input that mirrors the OpenAI gpt-image-2 v2 contract.
//
// Backend is the source of truth (``app/adapters/openai_v1.py:validate_custom_size``);
// this validator is a strict mirror so the user gets immediate feedback
// instead of a 422 round-trip.
//
// Catalog stays in lock-step with the upstream-quirks finding from
// commit history: every entry below passes all five rules. New rows
// must run through ``isValidSize`` before being added.

const SIZE_LIMITS = Object.freeze({
  multiple: 16,
  maxEdge: 3840,
  minPixels: 655_360,
  maxPixels: 8_294_400,
  maxRatio: 3,
  experimentalEdge: 2560,  // longest-edge threshold for the "experimental" tag
  experimentalShort: 1440, // shorter-edge threshold for the experimental tag
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

// Curated catalog. Each row passes ``isValidSize``. Groups appear in
// the order users tend to pick them — common aspect ratios first.
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
      { label: "Landscape (small)", w: 1280, h: 960 },
      { label: "Portrait (small)", w: 960, h: 1280 },
      { label: "Landscape (medium)", w: 1600, h: 1200 },
      { label: "Portrait (medium)", w: 1200, h: 1600 },
    ],
  },
  {
    title: "4:5 / 5:4",
    note: "Instagram, posters",
    items: [
      { label: "IG portrait", w: 1024, h: 1280 },
      { label: "IG portrait (large)", w: 1280, h: 1600 },
      { label: "5:4 landscape", w: 1280, h: 1024 },
    ],
  },
  {
    title: "21:9",
    note: "ultrawide, cinematic",
    items: [
      { label: "Cinema (small)", w: 1680, h: 720 },
      { label: "Cinema (medium)", w: 2240, h: 960 },
      { label: "Cinema (large)", w: 3360, h: 1440 },
    ],
  },
  {
    title: "3:1",
    note: "extreme banners — note: some relays clamp ratio at 2:1",
    items: [
      { label: "Banner (small)", w: 1536, h: 512 },
      { label: "Banner (medium)", w: 2304, h: 768 },
      { label: "Banner (max)", w: 3840, h: 1280 },
    ],
  },
  {
    title: "Square / 4K",
    note: "experimental, may take longer to render",
    items: [
      { label: "Square (large)", w: 1280, h: 1280 },
      { label: "Square (2K)", w: 2048, h: 2048 },
      { label: "4K UHD", w: 3840, h: 2160 },
    ],
  },
];

function summariseSize(w, h) {
  const px = (w * h) / 1_000_000;
  return `${w}×${h} · ${px.toFixed(2)} MP`;
}

export default function SizeCustomModal({ open, initialValue, onSelect, onClose }) {
  const [w, setW] = useState("");
  const [h, setH] = useState("");
  const [touched, setTouched] = useState(false);
  const firstButtonRef = useRef(null);
  const wInputRef = useRef(null);

  // Pre-fill the W/H inputs when a custom value is already selected.
  useEffect(() => {
    if (!open) {
      setTouched(false);
      return;
    }
    if (typeof initialValue === "string" && initialValue.match(/^\d+x\d+$/i)) {
      const [iw, ih] = initialValue.split(/x/i);
      setW(iw);
      setH(ih);
    } else {
      setW("");
      setH("");
    }
    // Focus the first picker chip for keyboard users.
    setTimeout(() => firstButtonRef.current?.focus(), 50);
  }, [open, initialValue]);

  // Esc closes; Enter on the manual input commits if valid.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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

  const pickFromCatalog = (item) => {
    onSelect?.(`${item.w}x${item.h}`);
  };

  const submitManual = () => {
    setTouched(true);
    if (!manualCheck.ok) return;
    onSelect?.(`${Number(w)}x${Number(h)}`);
  };

  const onWChange = (e) => {
    setW(e.target.value.replace(/[^\d]/g, ""));
    setTouched(true);
  };
  const onHChange = (e) => {
    setH(e.target.value.replace(/[^\d]/g, ""));
    setTouched(true);
  };
  const onManualKey = (e) => {
    if (e.key === "Enter") submitManual();
  };

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
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <style>{`
        @keyframes scsFade { from { opacity: 0; } to { opacity: 1; } }
        @keyframes scsPop { from { opacity: 0; transform: translateY(8px) scale(.98); } to { opacity: 1; transform: none; } }
      `}</style>
      <div
        style={{
          width: 720,
          maxHeight: "88vh",
          background: "var(--paper)",
          border: "1px solid var(--ink)",
          boxShadow: "8px 8px 0 var(--ink)",
          animation: "scsPop 220ms cubic-bezier(.2,.85,.2,1)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            padding: "14px 20px",
            background: "var(--ink)",
            color: "var(--paper)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span
            className="mono caps"
            style={{ fontSize: 11, letterSpacing: "0.18em" }}
          >
            CUSTOM SIZE · gpt-image-2
          </span>
          <button
            data-testid="size-custom-close"
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--paper)",
              cursor: "pointer",
              padding: 4,
            }}
            aria-label="Close size picker"
          >
            <Icon name="close" size={14} />
          </button>
        </div>

        <div
          style={{
            padding: "20px 24px 4px",
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}
          >
            STEP 1 — pick from a curated, fully-compliant catalog
          </div>
          <p
            style={{
              marginTop: 8,
              fontSize: 12,
              lineHeight: 1.55,
              color: "var(--ink-2)",
              maxWidth: 560,
            }}
          >
            Every option below satisfies OpenAI's contract: 16-multiple
            sides, longest edge ≤ 3840px, total pixels in [655 360 –
            8 294 400], aspect ratio ≤ 3:1.
          </p>
        </div>

        <div
          style={{
            padding: "8px 24px 16px",
            overflowY: "auto",
            flex: 1,
          }}
        >
          {CATALOG.map((group, gi) => (
            <div key={group.title} style={{ marginTop: gi === 0 ? 0 : 14 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  justifyContent: "space-between",
                  marginBottom: 6,
                }}
              >
                <div
                  className="mono caps"
                  style={{ fontSize: 10, color: "var(--ink-3)" }}
                >
                  {group.title}
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  {group.note}
                </div>
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  gap: 6,
                }}
              >
                {group.items.map((item, idx) => {
                  const v = isValidSize(item.w, item.h);
                  const sizeStr = `${item.w}x${item.h}`;
                  const matches = initialValue === sizeStr;
                  return (
                    <button
                      key={item.label}
                      ref={
                        gi === 0 && idx === 0 ? firstButtonRef : undefined
                      }
                      onClick={() => pickFromCatalog(item)}
                      data-testid={`size-preset-${sizeStr}`}
                      style={{
                        textAlign: "left",
                        padding: "8px 10px",
                        background: matches ? "var(--ink)" : "var(--paper-soft)",
                        color: matches ? "var(--paper)" : "var(--ink)",
                        border: "1px solid var(--ink)",
                        cursor: "pointer",
                        fontFamily: "inherit",
                      }}
                    >
                      <div
                        className="mono"
                        style={{
                          fontSize: 12,
                          fontWeight: 700,
                        }}
                      >
                        {item.label}
                        {v.experimental ? (
                          <span
                            className="mono caps"
                            style={{
                              marginLeft: 6,
                              padding: "1px 6px",
                              fontSize: 8,
                              background: matches ? "var(--banana)" : "var(--banana-soft)",
                              color: "var(--ink)",
                              letterSpacing: "0.1em",
                            }}
                          >
                            EXP
                          </span>
                        ) : null}
                      </div>
                      <div
                        className="mono"
                        style={{
                          marginTop: 2,
                          fontSize: 10,
                          color: matches ? "var(--paper)" : "var(--ink-3)",
                        }}
                      >
                        {summariseSize(item.w, item.h)}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        <div
          style={{
            padding: "16px 24px",
            borderTop: "1px solid var(--rule-2)",
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}
          >
            STEP 2 — or type your own
          </div>
          <div
            style={{
              marginTop: 10,
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexWrap: "wrap",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <input
                data-testid="size-custom-width"
                ref={wInputRef}
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={w}
                onChange={onWChange}
                onKeyDown={onManualKey}
                placeholder="width"
                aria-label="Custom width"
                style={{
                  width: 90,
                  padding: "8px 10px",
                  border: "1px solid var(--ink)",
                  background: "var(--paper)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                }}
              />
              <span
                className="mono"
                style={{ fontSize: 14, color: "var(--ink-2)" }}
              >
                ×
              </span>
              <input
                data-testid="size-custom-height"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={h}
                onChange={onHChange}
                onKeyDown={onManualKey}
                placeholder="height"
                aria-label="Custom height"
                style={{
                  width: 90,
                  padding: "8px 10px",
                  border: "1px solid var(--ink)",
                  background: "var(--paper)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                }}
              />
              <span
                className="mono"
                style={{ fontSize: 12, color: "var(--ink-3)", marginLeft: 6 }}
              >
                px
              </span>
            </div>
            <button
              data-testid="size-custom-apply"
              onClick={submitManual}
              disabled={!manualCheck.ok}
              className="btn ink shadowed"
              style={{
                height: 40,
                padding: "0 18px",
                color: "var(--banana)",
                opacity: manualCheck.ok ? 1 : 0.5,
                cursor: manualCheck.ok ? "pointer" : "not-allowed",
              }}
            >
              Use this size →{" "}
              <span
                className="kbd"
                style={{
                  marginLeft: 6,
                  background: "var(--banana)",
                  color: "var(--ink)",
                }}
              >
                ↵
              </span>
            </button>
          </div>
          <div
            data-testid="size-custom-validation"
            className="mono"
            style={{
              marginTop: 10,
              fontSize: 11,
              minHeight: 16,
              color: manualCheck.empty
                ? "var(--ink-3)"
                : manualCheck.ok
                ? "var(--banana-deep)"
                : "#c0392b",
            }}
          >
            {manualCheck.empty
              ? "Enter both width and height. Both must be multiples of 16."
              : manualCheck.ok
              ? `${summariseSize(Number(w), Number(h))} · ${
                  manualCheck.experimental ? "experimental zone" : "stable zone"
                } — looks good.`
              : touched
              ? manualCheck.reason
              : ""}
          </div>
        </div>

        <div
          style={{
            padding: "12px 24px 18px",
            display: "flex",
            justifyContent: "flex-end",
          }}
        >
          <button
            onClick={onClose}
            className="btn"
            style={{ height: 40, padding: "0 22px" }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
