import { useCallback, useEffect, useRef, useState } from "react";

// OutputCountSlider — Claude Design plan B (slider).
//
// Replaces the legacy NumberPresets ticker+chip combo used on the Create
// page right rail. Props match NumberPresets 1:1 so the FieldRenderer call
// site doesn't change. ``max`` (== caps.n_max) is dynamic; the slider
// range, axis labels, and MAX shortcut all scale to it. When ``max`` is
// null or 1 the whole control greys out — the right rail must keep its
// position so the layout doesn't collapse.

const MIN = 1;

function clamp(n, min, max) {
  if (Number.isNaN(n)) return null;
  if (n < min) return min;
  if (n > max) return max;
  return n;
}

export default function OutputCountSlider({
  label,
  hint,
  presets,
  max,
  value,
  onChange,
  fieldDisabled = false,
  disabledReason = null,
}) {
  const hardDisabled =
    fieldDisabled || typeof max !== "number" || max < 1 || max === 1;
  const effectiveMax = typeof max === "number" && max >= 1 ? max : 1;
  const display = clamp(typeof value === "number" ? value : 1, MIN, effectiveMax) ?? 1;

  // Local editable string so the user can clear the input mid-type.
  const [inputText, setInputText] = useState(String(display));
  useEffect(() => {
    setInputText(String(display));
  }, [display]);

  // Clamp warning: shown briefly when the user enters / drags to a value
  // beyond ``max``. Mirrors the design prototype's ``.clamp`` toast.
  const [clampShown, setClampShown] = useState(false);
  const clampTimer = useRef(null);
  const showClamp = useCallback(() => {
    setClampShown(true);
    if (clampTimer.current) clearTimeout(clampTimer.current);
    clampTimer.current = setTimeout(() => setClampShown(false), 1400);
  }, []);
  useEffect(() => () => {
    if (clampTimer.current) clearTimeout(clampTimer.current);
  }, []);

  const commit = useCallback(
    (n, opts = {}) => {
      if (hardDisabled) return;
      const parsed = typeof n === "number" ? n : parseInt(n, 10);
      if (Number.isNaN(parsed)) return;
      const next = clamp(parsed, MIN, effectiveMax);
      if (next == null) return;
      if (parsed > effectiveMax || (opts.markClamp && parsed !== next)) {
        showClamp();
      }
      if (next !== value) {
        onChange(next);
      } else {
        // Keep the input text in sync when the controlled value didn't change
        setInputText(String(next));
      }
    },
    [effectiveMax, hardDisabled, onChange, showClamp, value]
  );

  const trackRef = useRef(null);
  const draggingRef = useRef(false);

  const pct = ((display - MIN) / Math.max(1, effectiveMax - MIN)) * 96 + 2;

  const fromClientX = useCallback(
    (x) => {
      const el = trackRef.current;
      if (!el) return MIN;
      const rect = el.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (x - rect.left) / Math.max(1, rect.width)));
      return Math.round(MIN + ratio * (effectiveMax - MIN));
    },
    [effectiveMax]
  );

  const onTrackDown = (e) => {
    if (hardDisabled) return;
    draggingRef.current = true;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch (_err) {
      // happens in some Playwright pointer flows; we still get the
      // pointermove events on the same element.
    }
    commit(fromClientX(e.clientX));
  };
  const onTrackMove = (e) => {
    if (!draggingRef.current || hardDisabled) return;
    commit(fromClientX(e.clientX));
  };
  const onTrackUp = () => {
    draggingRef.current = false;
  };

  // Preset/MAX row. Distinct values: user-provided presets that fit under
  // max (deduped) followed by MAX = effectiveMax. The MAX button is
  // always rendered, per spec.
  const presetSource = Array.isArray(presets) && presets.length
    ? presets
    : [1, 2, 4, 8];
  const seen = new Set();
  const safePresets = [];
  for (const n of presetSource) {
    if (typeof n !== "number" || n < MIN) continue;
    if (seen.has(n)) continue;
    seen.add(n);
    safePresets.push(n);
  }
  if (!seen.has(effectiveMax)) safePresets.push(effectiveMax);
  // Spec: MAX shortcut is the last button and represents ``max``. Pull
  // its value out so the rendered label can read "MAX" instead of the
  // raw number; non-MAX duplicates have already been filtered above.
  const maxValue = effectiveMax;

  // Axis tick labels — scale 1 / ⌈max/3⌉ / ⌈2*max/3⌉ / max so the user
  // gets four evenly-spaced anchors regardless of n_max. When max is
  // small (≤4) we collapse to "1 / max" to avoid duplicate labels.
  const axisStops = (() => {
    if (effectiveMax <= 1) return [1];
    if (effectiveMax <= 4) return [1, effectiveMax];
    return [
      1,
      Math.ceil(effectiveMax / 3),
      Math.ceil((2 * effectiveMax) / 3),
      effectiveMax,
    ];
  })();

  // Major ticks at both ends; minor ticks at every other integer so we
  // never crowd the rail when max is large (e.g. n_max=64).
  const tickStep = effectiveMax <= 16 ? 1 : Math.max(1, Math.ceil(effectiveMax / 14));

  return (
    <div
      data-testid="create-output-count-slider"
      data-value={display}
      data-max={typeof max === "number" ? max : ""}
      title={hardDisabled ? disabledReason || undefined : undefined}
      style={{ opacity: hardDisabled ? 0.55 : 1 }}
    >
      <div
        className="mono caps"
        style={{
          fontSize: 10,
          color: hardDisabled ? "var(--ink-4)" : "var(--ink-3)",
          marginBottom: 6,
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          letterSpacing: "0.14em",
        }}
      >
        <span>{label}</span>
        <span style={{ fontSize: 9, color: "var(--ink-4)" }}>
          max {effectiveMax}
        </span>
      </div>

      <div
        style={{
          border: "1px solid var(--ink)",
          // Match the legacy NumberPresets' 12px inner padding so this
          // control occupies the exact same column width — important
          // because every other right-rail field is calibrated against
          // that footprint.
          padding: "10px 12px 8px",
          background: hardDisabled ? "var(--paper-3)" : "#fffdf7",
        }}
      >
        {/* Top: ticker + editable number on the left, meta on the right */}
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            marginBottom: 6,
          }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 3 }}>
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 700,
                fontSize: 18,
                color: "var(--ink-3)",
                letterSpacing: "-0.02em",
              }}
            >
              ×
            </span>
            <input
              data-testid="create-output-count-input"
              type="number"
              min={MIN}
              max={effectiveMax}
              value={inputText}
              disabled={hardDisabled}
              inputMode="numeric"
              onChange={(e) => {
                const raw = e.target.value.replace(/[^0-9]/g, "");
                setInputText(raw);
                if (raw === "") return;
                const n = parseInt(raw, 10);
                if (Number.isNaN(n)) return;
                commit(n, { markClamp: n > effectiveMax });
              }}
              onBlur={() => {
                if (inputText === "" || parseInt(inputText, 10) < MIN) {
                  commit(MIN);
                }
              }}
              onFocus={(e) => e.currentTarget.select()}
              onKeyDown={(e) => {
                // Read from ``display`` (derived from the controlled
                // ``value`` prop) — ``inputText`` lags behind rapid
                // keypresses and would swallow steps.
                if (e.key === "ArrowUp") {
                  e.preventDefault();
                  commit(display + 1);
                }
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  commit(display - 1);
                }
              }}
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 900,
                fontSize: 30,
                lineHeight: 1,
                letterSpacing: "-0.04em",
                background: "transparent",
                border: "none",
                outline: "none",
                padding: 0,
                width: 52,
                textAlign: "left",
                color: "var(--ink)",
                fontVariantNumeric: "tabular-nums",
                MozAppearance: "textfield",
              }}
            />
            {/* Hidden mirror so e2e tests have a static current-value node */}
            <span
              data-testid="create-output-count-value"
              style={{ position: "absolute", left: -9999, top: -9999 }}
              aria-hidden="true"
            >
              ×{display}
            </span>
          </div>

          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              textTransform: "uppercase",
              letterSpacing: "0.12em",
              color: "var(--ink-3)",
              textAlign: "right",
              lineHeight: 1.2,
            }}
          >
            <div>
              of <b style={{ color: "var(--ink)" }}>{effectiveMax}</b>
            </div>
            <div style={{ color: "var(--ink-4)" }}>images</div>
          </div>
        </div>

        {/* Slider track */}
        <div
          data-testid="create-output-count-track"
          ref={trackRef}
          onPointerDown={onTrackDown}
          onPointerMove={onTrackMove}
          onPointerUp={onTrackUp}
          onPointerCancel={onTrackUp}
          style={{
            position: "relative",
            height: 20,
            margin: "0 -2px",
            cursor: hardDisabled ? "not-allowed" : "pointer",
            userSelect: "none",
            touchAction: "none",
          }}
        >
          {/* Rail */}
          <div
            style={{
              position: "absolute",
              left: 2,
              right: 2,
              top: "50%",
              height: 2,
              background: "var(--ink)",
              transform: "translateY(-50%)",
            }}
          />
          {/* Fill */}
          <div
            style={{
              position: "absolute",
              left: 2,
              top: "50%",
              height: 5,
              background: "var(--banana)",
              border: "1px solid var(--ink)",
              transform: "translateY(-50%)",
              width: `${Math.max(0, pct - 2)}%`,
              transition: draggingRef.current ? "none" : "width 120ms ease-out",
            }}
          />
          {/* Ticks */}
          <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            {Array.from({ length: effectiveMax }).map((_, i) => {
              const n = i + 1;
              const isMajor = n === MIN || n === effectiveMax;
              if (!isMajor && (n - MIN) % tickStep !== 0) return null;
              const leftPct = ((n - MIN) / Math.max(1, effectiveMax - MIN)) * 96 + 2;
              return (
                <span
                  key={n}
                  style={{
                    position: "absolute",
                    top: "50%",
                    transform: "translate(-50%, -50%)",
                    width: 1,
                    height: isMajor ? 11 : 7,
                    background: "var(--ink)",
                    opacity: isMajor ? 1 : 0.45,
                    left: `${leftPct}%`,
                  }}
                />
              );
            })}
          </div>
          {/* Thumb */}
          <div
            data-testid="create-output-count-thumb"
            role="slider"
            aria-valuemin={MIN}
            aria-valuemax={effectiveMax}
            aria-valuenow={display}
            tabIndex={hardDisabled ? -1 : 0}
            style={{
              position: "absolute",
              top: "50%",
              transform: "translate(-50%, -50%)",
              width: 16,
              height: 16,
              background: "var(--ink)",
              border: "1px solid var(--ink)",
              cursor: hardDisabled ? "not-allowed" : "grab",
              left: `${pct}%`,
              transition: draggingRef.current ? "none" : "left 120ms ease-out",
            }}
          >
            <span
              style={{
                position: "absolute",
                inset: 3,
                background: "var(--banana)",
                display: "block",
              }}
            />
          </div>
        </div>

        {/* Axis labels */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "var(--ink-4)",
            marginTop: 4,
          }}
        >
          {axisStops.map((n) => (
            <span key={n}>{n}</span>
          ))}
        </div>

        {/* Preset / MAX row */}
        <div style={{ display: "flex", gap: 4, marginTop: 8 }}>
          {safePresets.map((n, idx) => {
            const isLast = idx === safePresets.length - 1;
            const allowed = !hardDisabled && n <= effectiveMax;
            const on = display === n;
            const isMax = isLast && n === maxValue;
            return (
              <button
                key={`${n}-${idx}`}
                data-testid={`create-output-count-preset-${n}`}
                data-allowed={allowed}
                onClick={() => allowed && commit(n)}
                disabled={!allowed}
                title={
                  hardDisabled
                    ? disabledReason || undefined
                    : allowed
                    ? undefined
                    : `caps at ${effectiveMax}`
                }
                style={{
                  flex: 1,
                  height: 22,
                  background: isMax
                    ? "var(--ink)"
                    : on
                    ? "var(--banana)"
                    : allowed
                    ? "transparent"
                    : "var(--paper-3)",
                  border: "1px solid var(--ink)",
                  cursor: allowed ? "pointer" : "not-allowed",
                  fontFamily: "var(--font-mono)",
                  fontSize: isMax ? 9 : 11,
                  letterSpacing: isMax ? "0.1em" : undefined,
                  textTransform: isMax ? "uppercase" : undefined,
                  fontWeight: 700,
                  color: isMax
                    ? "var(--banana)"
                    : allowed
                    ? "var(--ink)"
                    : "var(--ink-4)",
                  opacity: allowed ? 1 : 0.5,
                  padding: 0,
                }}
              >
                {isMax ? "MAX" : n}
              </button>
            );
          })}
        </div>

        {/* Clamp toast — collapses to 0 height when hidden so the panel
            doesn't reserve dead space for it. */}
        <div
          data-testid="create-output-count-clamp"
          data-visible={clampShown ? "true" : "false"}
          style={{
            marginTop: clampShown ? 6 : 0,
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--warn, #c0392b)",
            display: "flex",
            alignItems: "center",
            gap: 6,
            opacity: clampShown ? 1 : 0,
            height: clampShown ? 12 : 0,
            overflow: "hidden",
            transition: "opacity 160ms, height 160ms, margin-top 160ms",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 12,
              height: 12,
              background: "var(--warn, #c0392b)",
              color: "#fffdf7",
              fontWeight: 800,
              fontSize: 9,
            }}
          >
            !
          </span>
          capped at {effectiveMax} — the max
        </div>
      </div>

      {hardDisabled && disabledReason ? (
        <div
          className="mono"
          style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 4 }}
        >
          {disabledReason}
        </div>
      ) : null}
    </div>
  );
}
