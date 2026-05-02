// Onboarding Tutorial — a six-step modal walkthrough shown on first
// login for non-admin users. Ported from the Claude Design "Onboarding
// Tutorial.html" handoff bundle.
//
// The modal is a self-contained React component: scenes live in this
// file as small subcomponents, shared atoms (Mono / Tag / Cursor /
// CornerLabel / progress row / loop hook) sit at the top, and the
// outer Modal manages step navigation, keyboard shortcuts, and the
// minimised "Replay tour" floating chip.
import { useCallback, useEffect, useState } from "react";

const STEPS = [
  { id: 1, kind: "WELCOME",  label: "Welcome" },
  { id: 2, kind: "STOP 01",  label: "Dashboard" },
  { id: 3, kind: "STOP 02a", label: "Create · prompt" },
  { id: 4, kind: "STOP 02b", label: "Create · dial in" },
  { id: 5, kind: "STOP 03",  label: "Archive" },
  { id: 6, kind: "DONE",     label: "Ready" },
];

export default function OnboardingTutorial({ open, onClose, onMinimise }) {
  const [step, setStep] = useState(0);

  const next = useCallback(
    () => setStep((s) => Math.min(STEPS.length - 1, s + 1)),
    [],
  );
  const prev = useCallback(() => setStep((s) => Math.max(0, s - 1)), []);
  const close = useCallback(() => {
    setStep(0);
    if (onClose) onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") {
        if (onMinimise) onMinimise();
        else close();
      }
      if (e.key === "ArrowRight" || e.key === "Enter") next();
      if (e.key === "ArrowLeft") prev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, next, prev, close, onMinimise]);

  // Reset to first step every time the modal opens so a "Replay tour"
  // user starts at the beginning rather than wherever they left off.
  useEffect(() => {
    if (open) setStep(0);
  }, [open]);

  if (!open) return null;

  const isFirst = step === 0;
  const isLast = step === STEPS.length - 1;
  const cur = STEPS[step];

  return (
    <div
      data-testid="onboarding-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Onboarding tour"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 100,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#19171477",
        backdropFilter: "blur(3px)",
        animation: "tFade 200ms ease-out",
      }}
    >
      <style>{`
        @keyframes tFade { from { opacity: 0; } to { opacity: 1; } }
        @keyframes tPop { from { opacity: 0; transform: translateY(10px) scale(.985); } to { opacity: 1; transform: none; } }
        @keyframes blink { 50% { opacity: 0; } }
        @keyframes refEmptyBreathe {
          0%, 100% { border-color: var(--ink); }
          50% { border-color: var(--ink-3); }
        }
        @keyframes refChipFloat {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-1.5px); }
        }
        @keyframes refGhostPulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 0.65; }
        }
      `}</style>

      <div
        style={{
          width: 920,
          maxWidth: "calc(100vw - 48px)",
          height: 600,
          maxHeight: "calc(100vh - 48px)",
          background: "var(--paper)",
          border: "1px solid var(--ink)",
          boxShadow: "10px 10px 0 var(--ink)",
          animation: "tPop 280ms cubic-bezier(.2,.85,.2,1)",
          display: "flex",
          flexDirection: "column",
          position: "relative",
        }}
      >
        {/* header */}
        <div
          style={{
            padding: "12px 18px",
            background: "var(--ink)",
            color: "var(--paper)",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div
            style={{
              width: 22,
              height: 22,
              background: "var(--banana)",
              border: "1px solid var(--banana)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              fontWeight: 800,
              color: "var(--ink)",
              letterSpacing: "-0.03em",
            }}
          >
            t2i
          </div>
          <span className="mono caps" style={{ fontSize: 10, letterSpacing: "0.18em" }}>
            Walkthrough · {cur.kind}
          </span>
          <span style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-4)" }}>
            {String(step + 1).padStart(2, "0")} / {String(STEPS.length).padStart(2, "0")}
          </span>
          <button
            type="button"
            onClick={onMinimise || close}
            aria-label="Close tour"
            data-testid="onboarding-close"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--paper)",
              cursor: "pointer",
              padding: 4,
              display: "flex",
            }}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <path d="M3 3l10 10M13 3L3 13" />
            </svg>
          </button>
        </div>

        {/* progress strip */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${STEPS.length}, 1fr)`,
            background: "var(--paper-2)",
            borderBottom: "1px solid var(--ink)",
          }}
        >
          {STEPS.map((s, i) => {
            const done = i < step;
            const active = i === step;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setStep(i)}
                style={{
                  padding: "8px 10px",
                  background: active
                    ? "var(--banana)"
                    : done
                      ? "var(--paper-3)"
                      : "transparent",
                  borderRight: i < STEPS.length - 1 ? "1px solid var(--ink)" : "none",
                  borderTop: "none",
                  borderBottom: "none",
                  borderLeft: "none",
                  cursor: "pointer",
                  textAlign: "left",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: active ? "var(--ink)" : done ? "var(--ink-2)" : "var(--ink-3)",
                  fontWeight: active ? 800 : 600,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  transition: "background 200ms",
                }}
              >
                <span
                  style={{
                    width: 14,
                    height: 14,
                    background:
                      active || done ? "var(--ink)" : "transparent",
                    color: active
                      ? "var(--banana)"
                      : done
                        ? "var(--paper)"
                        : "var(--ink-3)",
                    border: "1px solid var(--ink)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 9,
                    fontWeight: 800,
                  }}
                >
                  {done ? "✓" : i + 1}
                </span>
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {s.label}
                </span>
              </button>
            );
          })}
        </div>

        {/* scene area */}
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          <SceneSwitch step={step} />
        </div>

        {/* footer */}
        <div
          style={{
            padding: "12px 20px",
            borderTop: "1px solid var(--ink)",
            background: "var(--paper-2)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <button
            type="button"
            onClick={close}
            data-testid="onboarding-skip"
            style={{
              background: "transparent",
              border: "none",
              fontFamily: "var(--font-sans)",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--ink-3)",
              cursor: "pointer",
              padding: "6px 4px",
            }}
          >
            Skip tour
          </button>
          <span style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            <span className="kbd" style={{ marginRight: 4 }}>←</span>
            <span className="kbd" style={{ marginRight: 8 }}>→</span>
            navigate
          </span>
          <button
            type="button"
            onClick={prev}
            disabled={isFirst}
            className="btn"
            style={{
              height: 38,
              padding: "0 16px",
              opacity: isFirst ? 0.35 : 1,
              cursor: isFirst ? "default" : "pointer",
            }}
          >
            ← Back
          </button>
          {!isLast ? (
            <button
              type="button"
              onClick={next}
              data-testid="onboarding-next"
              className="btn ink shadowed"
              style={{ height: 38, padding: "0 18px", color: "var(--banana)" }}
            >
              Next →
              <span
                className="kbd"
                style={{ marginLeft: 6, background: "var(--banana)", color: "var(--ink)" }}
              >
                ↵
              </span>
            </button>
          ) : (
            <button
              type="button"
              onClick={close}
              data-testid="onboarding-finish"
              className="btn primary shadowed"
              style={{ height: 38, padding: "0 18px" }}
            >
              <svg width="14" height="14" viewBox="0 0 16 16">
                <path d="M9 2L3 9h4l-1 5 6-7H8l1-5z" fill="var(--ink)" />
              </svg>
              Start creating
              <span
                className="kbd"
                style={{ marginLeft: 6, background: "var(--ink)", color: "var(--banana)" }}
              >
                ↵
              </span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scene switcher — keeps the active scene mounted only, with a soft
// horizontal-slide cross-fade between steps.
// ---------------------------------------------------------------------------

function SceneSwitch({ step }) {
  const Comps = [
    SceneWelcome,
    SceneDashboard,
    SceneCreatePrompt,
    SceneCreateShape,
    SceneArchive,
    SceneReady,
  ];
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      {Comps.map((C, i) => {
        const active = i === step;
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              inset: 0,
              opacity: active ? 1 : 0,
              transform: active
                ? "translateX(0)"
                : i < step
                  ? "translateX(-12px)"
                  : "translateX(12px)",
              transition:
                "opacity 280ms cubic-bezier(.2,.85,.2,1), transform 320ms cubic-bezier(.2,.85,.2,1)",
              pointerEvents: active ? "auto" : "none",
            }}
          >
            {active && <C active={active} />}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared atoms
// ---------------------------------------------------------------------------

function Mono({ children, style, ...rest }) {
  return (
    <span
      className="mono caps"
      style={{
        fontSize: 10,
        color: "var(--ink-3)",
        letterSpacing: "0.18em",
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  );
}

function Tag({ children, color = "var(--ink)", bg = "#fffdf7", style }) {
  return (
    <span
      className="mono"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 7px",
        border: `1px solid ${color}`,
        background: bg,
        color,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        ...style,
      }}
    >
      {children}
    </span>
  );
}

function CornerLabel({ children }) {
  return (
    <div
      style={{
        position: "absolute",
        top: -8,
        left: -8,
        zIndex: 5,
        width: 22,
        height: 22,
        background: "var(--banana)",
        border: "1px solid var(--ink)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "var(--font-display)",
        fontSize: 13,
        fontWeight: 900,
        fontStyle: "italic",
        boxShadow: "2px 2px 0 var(--ink)",
      }}
    >
      {children}
    </div>
  );
}

function Cursor({ x, y, label, show = true }) {
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        transform: "translate(-2px,-2px)",
        pointerEvents: "none",
        transition:
          "left 700ms cubic-bezier(.4,.9,.3,1.05), top 700ms cubic-bezier(.4,.9,.3,1.05), opacity 200ms",
        opacity: show ? 1 : 0,
        zIndex: 30,
      }}
    >
      <svg width="20" height="22" viewBox="0 0 20 22">
        <path
          d="M2 2 L2 18 L7 14 L10 20 L13 19 L10 13 L17 13 Z"
          fill="var(--ink)"
          stroke="var(--paper)"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </svg>
      {label && (
        <div
          className="mono"
          style={{
            position: "absolute",
            left: 18,
            top: 18,
            padding: "3px 7px",
            background: "var(--ink)",
            color: "var(--banana)",
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: "0.1em",
            whiteSpace: "nowrap",
            textTransform: "uppercase",
          }}
        >
          {label}
        </div>
      )}
    </div>
  );
}

function ProgRow({ value, max = 1, label }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 60px",
        gap: 8,
        alignItems: "center",
        marginTop: 4,
      }}
    >
      <div style={{ height: 8, background: "var(--banana-soft)", position: "relative" }}>
        <div
          style={{
            position: "absolute",
            inset: 0,
            width: `${(value / max) * 100}%`,
            background: "var(--banana-deep)",
            transition: "width 80ms linear",
          }}
        />
      </div>
      <span
        className="mono"
        style={{ fontSize: 10, color: "var(--ink-3)", textAlign: "right" }}
      >
        {label}
      </span>
    </div>
  );
}

function Row({ k, v, hot }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "50px 1fr",
        gap: 8,
        padding: "4px 6px",
        background: hot ? "var(--banana-soft)" : "transparent",
        borderLeft: hot
          ? "2px solid var(--banana-deep)"
          : "2px solid transparent",
        transition: "background 240ms, border-color 240ms",
      }}
    >
      <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
        {k}
      </span>
      <span style={{ fontSize: 11, fontWeight: hot ? 700 : 500 }}>{v}</span>
    </div>
  );
}

// Loop a 0..1 progress on a fixed period (ms). Used by scenes with
// continuous looping demonstrations (typewriter, drop-ins, etc).
function useLoop(period, deps) {
  const [t, setT] = useState(0);
  useEffect(() => {
    let raf;
    let start;
    const tick = (ts) => {
      if (!start) start = ts;
      const p = ((ts - start) % period) / period;
      setT(p);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps || []);
  return t;
}

// ---------------------------------------------------------------------------
// SCENE 1 — WELCOME
// ---------------------------------------------------------------------------

function SceneWelcome({ active }) {
  const t = useLoop(5200, [active]);
  const stops = [
    { id: "01", title: "Dashboard", line: "see what's running, jump back in" },
    { id: "02", title: "Create", line: "type · pick · generate" },
    { id: "03", title: "Archive", line: "everything, searchable" },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1.05fr 1fr", height: "100%" }}>
      <div
        style={{
          padding: "44px 44px 36px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
        }}
      >
        <div>
          <Mono>Welcome to txt2img · Studio</Mono>
          <h1
            className="display"
            style={{
              fontSize: 64,
              fontWeight: 900,
              letterSpacing: "-0.045em",
              lineHeight: 0.94,
              margin: "18px 0 0",
            }}
          >
            A 90-second
            <br />
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>tour</span>, then
            <br />
            you're flying.
          </h1>
          <p
            style={{
              marginTop: 22,
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 17,
              color: "var(--ink-2)",
              lineHeight: 1.45,
              maxWidth: 360,
            }}
          >
            We'll follow one image — three overripe bananas on a marble slab —
            from prompt to keeper.
          </p>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Mono>Case study</Mono>
          <span style={{ flex: 1, height: 1, background: "var(--ink)", opacity: 0.2 }} />
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-2)" }}>
            "flemish marble · banana still-life"
          </span>
        </div>
      </div>

      <div
        style={{
          padding: "44px 36px 36px 12px",
          display: "flex",
          flexDirection: "column",
          gap: 14,
          justifyContent: "center",
        }}
      >
        {stops.map((s, i) => {
          const phase = t * 3 - i;
          const lit = phase > 0 && phase < 1;
          const passed = phase >= 1;
          return (
            <div
              key={s.id}
              style={{
                display: "grid",
                gridTemplateColumns: "60px 1fr 28px",
                gap: 14,
                alignItems: "center",
              }}
            >
              <div
                style={{
                  width: 60,
                  height: 60,
                  background: lit ? "var(--banana)" : passed ? "var(--ink)" : "#fffdf7",
                  color: lit ? "var(--ink)" : passed ? "var(--banana)" : "var(--ink)",
                  border: "1px solid var(--ink)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontFamily: "var(--font-display)",
                  fontSize: 32,
                  fontWeight: 900,
                  fontStyle: "italic",
                  boxShadow: lit ? "4px 4px 0 var(--ink)" : "none",
                  transition: "all 280ms cubic-bezier(.2,.85,.2,1)",
                  transform: lit ? "translate(-2px,-2px)" : "none",
                }}
              >
                {s.id}
              </div>
              <div>
                <div
                  className="display"
                  style={{ fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em" }}
                >
                  {s.title}
                </div>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    color: "var(--ink-3)",
                    marginTop: 4,
                    letterSpacing: "0.04em",
                  }}
                >
                  {s.line}
                </div>
              </div>
              <div
                style={{
                  height: 8,
                  position: "relative",
                  opacity: lit ? 1 : 0.25,
                  transition: "opacity 200ms",
                }}
              >
                <div
                  style={{
                    width: 8,
                    height: 8,
                    background: "var(--ink)",
                    transform: `translateX(${lit ? 12 : 0}px)`,
                    transition: "transform 600ms",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SCENE 2 — DASHBOARD
// ---------------------------------------------------------------------------

function SceneDashboard({ active }) {
  const t = useLoop(6000, [active]);
  const a = Math.min(1, t * 1.4);
  const b = Math.min(1, Math.max(0, t * 1.4 - 0.3));
  const picks = ["#9bdac5", "#e8a98a", "#1c1b18", "#f0c2db", "#f5c862"];
  const cx = 60 + t * 240;
  const cy = 110 + Math.sin(t * Math.PI * 2) * 8;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100%" }}>
      <div
        style={{
          padding: "40px 28px 32px 44px",
          borderRight: "1px solid var(--ink)",
          background: "var(--paper-2)",
        }}
      >
        <Tag bg="var(--banana)" color="var(--ink)">STOP 01 · DASHBOARD</Tag>
        <h2
          className="display"
          style={{
            fontSize: 38,
            fontWeight: 900,
            letterSpacing: "-0.035em",
            lineHeight: 1,
            margin: "16px 0 12px",
          }}
        >
          Pick up where you
          <br />
          <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>left off.</span>
        </h2>
        <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--ink-2)", marginTop: 8 }}>
          Your home page. The big card resumes whatever you were last doing.
          Below it, jobs in flight and recent keepers.
        </p>

        <div style={{ marginTop: 22, display: "flex", flexDirection: "column", gap: 10 }}>
          {[
            { k: "A", t: "Session card", d: "click Resume to jump back" },
            { k: "B", t: "In-flight bars", d: "live progress per batch" },
            { k: "C", t: "Recent picks", d: "your last 7 keepers" },
          ].map((r) => (
            <div
              key={r.k}
              style={{
                display: "grid",
                gridTemplateColumns: "20px 1fr",
                gap: 10,
                alignItems: "baseline",
              }}
            >
              <span
                className="mono"
                style={{ fontSize: 11, fontWeight: 800, color: "var(--banana-deep)" }}
              >
                {r.k}
              </span>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700 }}>{r.t}</div>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 2 }}>
                  {r.d}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ padding: "32px 36px", position: "relative", overflow: "hidden" }}>
        <Mono style={{ fontSize: 9 }}>TXT2IMG · WED 24 APR · GOOD AFTERNOON</Mono>
        <div
          className="display"
          style={{
            fontSize: 22,
            fontWeight: 900,
            letterSpacing: "-0.03em",
            lineHeight: 1,
            marginTop: 6,
          }}
        >
          Pick up where you{" "}
          <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>left off.</span>
        </div>

        <div
          style={{
            position: "relative",
            marginTop: 14,
            background: "var(--ink)",
            color: "var(--paper)",
            display: "grid",
            gridTemplateColumns: "100px 1fr",
            border: "1px solid var(--ink)",
          }}
        >
          <CornerLabel>A</CornerLabel>
          <div
            style={{
              background: "#37352f",
              backgroundImage:
                "repeating-linear-gradient(135deg,#3a3631,#3a3631 6px,#403c36 6px,#403c36 12px)",
              borderRight: "1px solid #4a463f",
            }}
          />
          <div style={{ padding: "14px 16px" }}>
            <div
              className="mono"
              style={{
                fontSize: 8,
                color: "#9c9488",
                letterSpacing: "0.16em",
                textTransform: "uppercase",
              }}
            >
              SESSION · EDITORIAL COVER · 24 IMAGES
            </div>
            <div
              className="display"
              style={{
                fontSize: 22,
                fontWeight: 800,
                letterSpacing: "-0.025em",
                margin: "4px 0 2px",
              }}
            >
              15 unreviewed
            </div>
            <div className="mono" style={{ fontSize: 9, color: "#9c9488" }}>
              started 18 min ago
            </div>
            <div style={{ marginTop: 8, display: "flex", gap: 6 }}>
              <button
                type="button"
                style={{
                  padding: "5px 12px",
                  background: "var(--banana)",
                  color: "var(--ink)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  fontWeight: 800,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  border: "1px solid var(--ink)",
                  boxShadow: "2px 2px 0 #000",
                }}
              >
                Resume ›
              </button>
              <button
                type="button"
                style={{
                  padding: "5px 12px",
                  background: "transparent",
                  color: "var(--paper)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  fontWeight: 700,
                  border: "1px solid var(--paper-2)",
                }}
              >
                Details
              </button>
            </div>
          </div>
        </div>

        <div
          style={{
            marginTop: 20,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 24,
          }}
        >
          <div style={{ position: "relative" }}>
            <CornerLabel>B</CornerLabel>
            <Mono style={{ fontSize: 8 }}>IN FLIGHT · 2 RUNNING</Mono>
            <div style={{ height: 1, background: "var(--ink)", marginTop: 6 }} />

            <div style={{ marginTop: 12 }}>
              <div className="mono" style={{ fontSize: 10 }}>
                <b>batch</b> · 印象派系列
              </div>
              <ProgRow value={a} max={1} label={`${Math.round(a * 20)}/20`} />
            </div>
            <div style={{ marginTop: 10 }}>
              <div className="mono" style={{ fontSize: 10 }}>
                <b>single</b> · marble still life
              </div>
              <ProgRow
                value={b}
                max={1}
                label={b < 0.05 ? "queued" : `${Math.round(b * 100)}%`}
              />
            </div>
          </div>

          <div style={{ position: "relative" }}>
            <CornerLabel>C</CornerLabel>
            <Mono style={{ fontSize: 8 }}>RECENT PICKS</Mono>
            <div style={{ height: 1, background: "var(--ink)", marginTop: 6 }} />
            <div
              style={{
                marginTop: 12,
                display: "grid",
                gridTemplateColumns: "repeat(5,1fr)",
                gap: 6,
              }}
            >
              {picks.map((c, i) => {
                const tInPick = t * 5 - i;
                const popped = tInPick > 0;
                return (
                  <div
                    key={i}
                    style={{
                      aspectRatio: "1/1",
                      background: c,
                      border: "1px solid var(--ink)",
                      transform: popped ? "translateY(0)" : "translateY(6px)",
                      opacity: popped ? 1 : 0,
                      transition: "transform 360ms, opacity 360ms",
                    }}
                  />
                );
              })}
            </div>
            <div className="mono" style={{ fontSize: 9, color: "var(--banana-deep)", marginTop: 6 }}>
              ★★★★★ ★★★★ ★★★★★ ★★★★ ★★★
            </div>
          </div>
        </div>

        <Cursor x={cx} y={cy} label="resume" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SCENE 3 — CREATE · PROMPT + REFS
// ---------------------------------------------------------------------------

const FULL_PROMPT =
  "A still-life of three overripe bananas on a marble slab, flemish painting, warm window light, 35mm";

function SceneCreatePrompt({ active }) {
  const [chars, setChars] = useState(0);
  useEffect(() => {
    if (!active) {
      setChars(0);
      return undefined;
    }
    setChars(0);
    let i = 0;
    const id = setInterval(() => {
      i += 2;
      if (i >= FULL_PROMPT.length) {
        setChars(FULL_PROMPT.length);
        clearInterval(id);
      } else {
        setChars(i);
      }
    }, 35);
    return () => clearInterval(id);
  }, [active]);

  const t = useLoop(6800, [active]);
  const refs = [
    { c: "#E8D5B0", l: "banana_01.jpg", appear: 0.42 },
    { c: "#4A5D3F", l: "leaf.png", appear: 0.56 },
    { c: "#C97B5E", l: "clay.jpg", appear: 0.7 },
  ];
  const visible = refs.map((r) => t > r.appear);
  const EMPTY_FADE = 0.06;
  const firstAppear = refs[0].appear;
  const emptyOpacity =
    t < firstAppear - EMPTY_FADE
      ? 1
      : t < firstAppear
        ? 1 - (t - (firstAppear - EMPTY_FADE)) / EMPTY_FADE
        : 0;
  const showEmpty = emptyOpacity > 0.001;
  const filledOpacity = visible.some(Boolean) ? 1 : 0;
  const showCursor = chars < FULL_PROMPT.length;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100%" }}>
      <div
        style={{
          padding: "40px 28px 32px 44px",
          borderRight: "1px solid var(--ink)",
          background: "var(--paper-2)",
        }}
      >
        <Tag bg="var(--banana)" color="var(--ink)">STOP 02a · CREATE — WRITE</Tag>
        <h2
          className="display"
          style={{
            fontSize: 36,
            fontWeight: 900,
            letterSpacing: "-0.035em",
            lineHeight: 1,
            margin: "16px 0 12px",
          }}
        >
          Make a <span style={{ fontStyle: "italic" }}>thing.</span>
        </h2>
        <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--ink-2)" }}>
          Describe what you want. Drop in up to <b>14 reference images</b> for
          vibe. Drafts autosave every keystroke.
        </p>

        <div
          style={{
            marginTop: 18,
            padding: "10px 12px",
            border: "1px solid var(--ink)",
            background: "#fffdf7",
          }}
        >
          <div
            className="mono"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.16em",
              textTransform: "uppercase",
            }}
          >
            Anatomy of a strong prompt
          </div>
          <div
            style={{
              marginTop: 8,
              display: "flex",
              flexDirection: "column",
              gap: 4,
              fontSize: 11,
              lineHeight: 1.4,
            }}
          >
            {[
              ["subject", "three overripe bananas"],
              ["scene", "on a marble slab"],
              ["style", "flemish painting"],
              ["light", "warm window light"],
              ["lens", "35mm"],
            ].map(([k, v]) => (
              <div
                key={k}
                style={{ display: "grid", gridTemplateColumns: "60px 1fr", gap: 8 }}
              >
                <span className="mono" style={{ color: "var(--ink-3)", fontSize: 10 }}>
                  {k}
                </span>
                <span style={{ color: "var(--ink)" }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{ padding: "28px 32px", position: "relative" }}>
        <Mono style={{ fontSize: 9 }}>CREATE / SINGLE JOB</Mono>

        <div
          style={{
            marginTop: 10,
            border: "1px solid var(--ink)",
            background: "#fffdf7",
            position: "relative",
          }}
        >
          <CornerLabel>A</CornerLabel>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "8px 14px",
              borderBottom: "1px solid var(--ink)",
              background: "var(--paper-2)",
            }}
          >
            <Mono style={{ fontSize: 9 }}>§ Prompt</Mono>
            <div style={{ display: "flex", gap: 4 }}>
              <Tag>ENHANCE</Tag>
              <Tag>{chars} chars</Tag>
            </div>
          </div>
          <div
            style={{
              padding: "18px 20px",
              fontFamily: "var(--font-display)",
              fontSize: 18,
              lineHeight: 1.4,
              color: "var(--ink)",
              letterSpacing: "-0.01em",
              minHeight: 92,
              position: "relative",
            }}
          >
            {FULL_PROMPT.slice(0, chars)}
            {showCursor && (
              <span
                style={{
                  display: "inline-block",
                  width: 2,
                  height: 22,
                  background: "var(--banana-deep)",
                  marginLeft: 2,
                  marginBottom: -4,
                  animation: "blink 600ms steps(1) infinite",
                }}
              />
            )}
          </div>
          <div
            style={{
              padding: "6px 14px",
              borderTop: "1px dashed var(--rule-2)",
              background: "var(--paper)",
              display: "flex",
              justifyContent: "space-between",
              fontSize: 10,
              color: "var(--ink-3)",
            }}
          >
            <span className="mono">Tip: subject · style · light · lens</span>
            <span className="mono">⌘↵ to generate</span>
          </div>
        </div>

        <div style={{ marginTop: 18, position: "relative" }}>
          <CornerLabel>B</CornerLabel>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              marginBottom: 8,
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <span
                className="display"
                style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" }}
              >
                Reference images
              </span>
              <Mono style={{ fontSize: 9 }}>{visible.filter(Boolean).length} / 14</Mono>
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              <Tag>UPLOAD</Tag>
              <Tag>PASTE ⌘V</Tag>
            </div>
          </div>

          <div style={{ position: "relative" }}>
            {showEmpty && (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(7, 1fr)",
                  gap: 6,
                  position: filledOpacity > 0 ? "absolute" : "relative",
                  inset: 0,
                  opacity: emptyOpacity,
                  transform: `translateY(${(1 - emptyOpacity) * -6}px) scale(${
                    0.985 + emptyOpacity * 0.015
                  })`,
                  transition:
                    "opacity 260ms ease, transform 260ms cubic-bezier(.2,.7,.2,1)",
                  pointerEvents: emptyOpacity > 0.5 ? "auto" : "none",
                }}
              >
                <div
                  style={{
                    gridColumn: "1 / 5",
                    aspectRatio: "4/1",
                    border: "1px dashed var(--ink)",
                    background:
                      "repeating-linear-gradient(45deg, transparent 0 10px, rgba(0,0,0,0.02) 10px 11px), #fffdf7",
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    padding: "0 14px",
                    animation: "refEmptyBreathe 2.4s ease-in-out infinite",
                  }}
                >
                  <div style={{ display: "flex", gap: 3, flexShrink: 0 }}>
                    {["#E8D5B0", "#C9B894", "#A89878"].map((c, i) => (
                      <div
                        key={i}
                        style={{
                          width: 22,
                          height: 22,
                          background: c,
                          border: "1px solid var(--ink)",
                          transform: `rotate(${(i - 1) * 4}deg)`,
                          boxShadow: "1px 1px 0 var(--ink)",
                          animation: `refChipFloat 2.4s ease-in-out ${i * 0.15}s infinite`,
                        }}
                      />
                    ))}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      className="display"
                      style={{
                        fontSize: 13,
                        fontWeight: 700,
                        letterSpacing: "-0.02em",
                        color: "var(--ink)",
                        lineHeight: 1.15,
                      }}
                    >
                      Drop reference imagery{" "}
                      <span style={{ fontStyle: "italic", color: "var(--ink-3)" }}>
                        — or skip.
                      </span>
                    </div>
                    <div
                      className="mono"
                      style={{ fontSize: 9, color: "var(--ink-3)", marginTop: 2 }}
                    >
                      Up to 14 images. Optional.
                    </div>
                  </div>
                </div>
                {[...Array(3)].map((_, i) => (
                  <div
                    key={i}
                    style={{
                      aspectRatio: "1/1",
                      border: "1px dashed var(--ink-4)",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 2,
                      opacity: 0.5,
                      animation: `refGhostPulse 2.4s ease-in-out ${i * 0.18}s infinite`,
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "var(--ink-4)",
                        lineHeight: 1,
                      }}
                    >
                      +
                    </span>
                    <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>
                      {i + 1}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(7, 1fr)",
                gap: 6,
                opacity: filledOpacity,
                transition: "opacity 260ms ease",
                visibility: filledOpacity > 0 ? "visible" : "hidden",
              }}
            >
              {refs.map((r, i) => (
                <div
                  key={i}
                  style={{
                    aspectRatio: "1/1",
                    border: "1px solid var(--ink)",
                    background: visible[i] ? r.c : "transparent",
                    position: "relative",
                    opacity: visible[i] ? 1 : 0,
                    transform: visible[i] ? "translateY(0)" : "translateY(-12px)",
                    transition: "all 320ms cubic-bezier(.2,.85,.2,1.1)",
                  }}
                >
                  {visible[i] && (
                    <div
                      style={{
                        position: "absolute",
                        bottom: 0,
                        left: 0,
                        right: 0,
                        padding: "2px 4px",
                        background: "var(--ink)",
                        color: "var(--paper)",
                        fontSize: 8,
                        fontFamily: "var(--font-mono)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {i + 1} · {r.l}
                    </div>
                  )}
                </div>
              ))}
              {[...Array(4)].map((_, i) => (
                <div
                  key={i}
                  style={{
                    aspectRatio: "1/1",
                    border: "1px dashed var(--ink-3)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    color: "var(--ink-4)",
                  }}
                >
                  {visible.filter(Boolean).length + i + 1}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SCENE 4 — CREATE · SHAPE / SIZE / COUNT / GENERATE
// ---------------------------------------------------------------------------

function SceneCreateShape({ active }) {
  const t = useLoop(6500, [active]);
  const phase = t < 0.22 ? 0 : t < 0.46 ? 1 : t < 0.72 ? 2 : 3;

  const shapes = [
    { r: "1:1", w: 22, h: 22 },
    { r: "4:3", w: 26, h: 20 },
    { r: "3:4", w: 20, h: 26 },
    { r: "16:9", w: 30, h: 18 },
  ];
  const sizes = [
    { id: "512", note: "preview" },
    { id: "1K", note: "balanced" },
    { id: "2K", note: "print" },
    { id: "4K", note: "max" },
  ];
  const counts = [1, 2, 4, 6, 8, 12];
  const shapeIdx = 0;
  const sizeIdx = 1;
  const countIdx = 2;
  const genPulse = phase === 3 ? Math.min(1, (t - 0.72) / 0.18) : 0;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100%" }}>
      <div
        style={{
          padding: "40px 28px 32px 44px",
          borderRight: "1px solid var(--ink)",
          background: "var(--paper-2)",
        }}
      >
        <Tag bg="var(--banana)" color="var(--ink)">STOP 02b · CREATE — DIAL IN</Tag>
        <h2
          className="display"
          style={{
            fontSize: 36,
            fontWeight: 900,
            letterSpacing: "-0.035em",
            lineHeight: 1,
            margin: "16px 0 12px",
          }}
        >
          Shape, size,
          <br />
          <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>send.</span>
        </h2>
        <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--ink-2)" }}>
          Pick an aspect, a resolution, and how many to generate at once. Hit{" "}
          <span className="kbd" style={{ verticalAlign: "1px" }}>⌘</span>
          <span className="kbd" style={{ verticalAlign: "1px" }}>↵</span> when ready.
        </p>

        <div
          style={{
            marginTop: 22,
            padding: "12px 14px",
            background: "#fffdf7",
            border: "1px solid var(--ink)",
          }}
        >
          <Mono style={{ fontSize: 9 }}>FOR THIS CASE</Mono>
          <div
            style={{
              marginTop: 8,
              display: "flex",
              flexDirection: "column",
              gap: 6,
              fontSize: 11,
            }}
          >
            <Row k="shape" v="1:1" hot={phase >= 0} />
            <Row k="size" v="1K · balanced" hot={phase >= 1} />
            <Row k="count" v="×4 outputs" hot={phase >= 2} />
            <Row k="model" v="ChatGPT Images 2.0" hot={phase >= 3} />
          </div>
        </div>
      </div>

      <div style={{ padding: "28px 32px", position: "relative" }}>
        <div
          className="display"
          style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-0.02em" }}
        >
          Shape
        </div>
        <div
          style={{
            marginTop: 8,
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 6,
          }}
        >
          {shapes.map((x, i) => {
            const on = i === shapeIdx && phase >= 0;
            return (
              <div
                key={x.r}
                style={{
                  padding: 10,
                  background: on ? "var(--ink)" : "#fffdf7",
                  color: on ? "var(--banana)" : "var(--ink)",
                  border: "1px solid var(--ink)",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 6,
                  transition: "all 200ms",
                }}
              >
                <div
                  style={{
                    width: x.w,
                    height: x.h,
                    background: on ? "var(--banana)" : "var(--paper-3)",
                    border: "1px solid currentColor",
                  }}
                />
                <span className="mono" style={{ fontSize: 10, fontWeight: 700 }}>
                  {x.r}
                </span>
              </div>
            );
          })}
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            marginTop: 16,
          }}
        >
          <div
            className="display"
            style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-0.02em" }}
          >
            Image size
          </div>
          <Mono style={{ fontSize: 9 }}>RESOLUTION / LONGEST EDGE</Mono>
        </div>
        <div
          style={{
            marginTop: 8,
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 6,
          }}
        >
          {sizes.map((x, i) => {
            const on = i === sizeIdx && phase >= 1;
            return (
              <div
                key={x.id}
                style={{
                  padding: "10px 8px",
                  background: on ? "var(--ink)" : "#fffdf7",
                  color: on ? "var(--banana)" : "var(--ink)",
                  border: "1px solid var(--ink)",
                  textAlign: "center",
                  transition: "all 200ms",
                }}
              >
                <div
                  className="ticker"
                  style={{
                    fontSize: 22,
                    fontWeight: 900,
                    letterSpacing: "-0.03em",
                    lineHeight: 1,
                  }}
                >
                  {x.id}
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 9, marginTop: 4, opacity: on ? 0.85 : 0.6 }}
                >
                  {x.note}
                </div>
              </div>
            );
          })}
        </div>

        <div
          className="display"
          style={{ marginTop: 18, fontSize: 18, fontWeight: 800, letterSpacing: "-0.02em" }}
        >
          Output count
        </div>
        <div
          style={{
            marginTop: 8,
            padding: 10,
            border: "1px solid var(--ink)",
            background: "#fffdf7",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            per generation
          </div>
          <div
            className="ticker"
            style={{ fontSize: 22, fontWeight: 900, letterSpacing: "-0.03em" }}
          >
            ×{counts[countIdx]}
          </div>
          <div style={{ flex: 1, display: "flex", gap: 3 }}>
            {counts.map((n, i) => {
              const on = i === countIdx && phase >= 2;
              return (
                <div
                  key={n}
                  style={{
                    flex: 1,
                    height: 26,
                    background: on ? "var(--banana)" : "transparent",
                    border: "1px solid var(--ink)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    fontWeight: 700,
                    transition: "background 200ms",
                  }}
                >
                  {n}
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ marginTop: 18, position: "relative" }}>
          <div
            style={{
              position: "relative",
              padding: "12px 16px",
              background: "var(--banana)",
              border: "1px solid var(--ink)",
              boxShadow: "3px 3px 0 var(--ink)",
              display: "flex",
              alignItems: "center",
              gap: 10,
              transform: phase === 3 ? "translate(1px,1px)" : "none",
              transition: "transform 120ms",
            }}
          >
            <svg width="14" height="14" viewBox="0 0 16 16">
              <path d="M9 2L3 9h4l-1 5 6-7H8l1-5z" fill="var(--ink)" />
            </svg>
            <span style={{ fontFamily: "var(--font-sans)", fontSize: 13, fontWeight: 700 }}>
              Generate ×4
            </span>
            <span style={{ flex: 1 }} />
            <span className="kbd" style={{ background: "var(--ink)", color: "var(--banana)" }}>
              ⌘↵
            </span>
            <div
              style={{
                position: "absolute",
                inset: 0,
                border: "2px solid var(--banana-deep)",
                opacity: 1 - genPulse,
                transform: `scale(${1 + genPulse * 0.18})`,
                pointerEvents: "none",
                transition: "none",
              }}
            />
          </div>
          <div
            className="mono"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              marginTop: 6,
              textAlign: "center",
            }}
          >
            A quick human-check appears if you're over today's quota.
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SCENE 5 — ARCHIVE
// ---------------------------------------------------------------------------

function SceneArchive({ active }) {
  const t = useLoop(6800, [active]);
  const tiles = [
    "#9bdac5", "#e8a98a", "#f5c862", "#3a3631",
    "#cf6638", "#3f6a26", "#f0c2db", "#bedb98",
    "#7fb6e3", "#5b2418", "#9bdac5", "#efe9d9",
    "#e8a98a", "#1c1b18", "#f5c862", "#bedb98",
  ];
  const visibleCount = Math.min(16, Math.floor((t * 16) / 0.42));
  const heroIdx = 4;
  const drawerOpen = t > 0.55;
  const cursorX = drawerOpen ? 240 : 80 + Math.sin(t * 7) * 6;
  const cursorY = drawerOpen ? 175 : 175;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100%" }}>
      <div
        style={{
          padding: "40px 28px 32px 44px",
          borderRight: "1px solid var(--ink)",
          background: "var(--paper-2)",
        }}
      >
        <Tag bg="var(--banana)" color="var(--ink)">STOP 03 · ARCHIVE</Tag>
        <h2
          className="display"
          style={{
            fontSize: 36,
            fontWeight: 900,
            letterSpacing: "-0.035em",
            lineHeight: 1,
            margin: "16px 0 12px",
          }}
        >
          Everything
          <br />
          you've{" "}
          <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>made.</span>
        </h2>
        <p style={{ fontSize: 13, lineHeight: 1.55, color: "var(--ink-2)" }}>
          Every job lives here forever. Filter by period, model, shape, status,
          rating, or session. Click any tile to open the side drawer.
        </p>

        <div
          style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 8 }}
        >
          <Mono style={{ fontSize: 9 }}>IN THE DRAWER</Mono>
          {[
            ["★ pick", "mark a keeper"],
            ["↻ retry", "regenerate same prompt"],
            ["⌥ recreate", "edit prompt & try again"],
            ["↗ share", "copy a public link"],
            ["⌘[ / ⌘]", "prev / next image"],
          ].map(([k, v]) => (
            <div
              key={k}
              style={{
                display: "grid",
                gridTemplateColumns: "70px 1fr",
                gap: 8,
                alignItems: "baseline",
              }}
            >
              <span
                className="mono"
                style={{ fontSize: 10, color: "var(--ink)", fontWeight: 700 }}
              >
                {k}
              </span>
              <span style={{ fontSize: 11, color: "var(--ink-2)" }}>{v}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ padding: "28px 32px", position: "relative", overflow: "hidden" }}>
        <Mono style={{ fontSize: 9 }}>HISTORY · ARCHIVE</Mono>
        <div
          className="display"
          style={{
            fontSize: 22,
            fontWeight: 900,
            letterSpacing: "-0.03em",
            marginTop: 4,
          }}
        >
          Everything you've made.
        </div>

        <div style={{ marginTop: 10, display: "flex", gap: 6, alignItems: "center" }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "4px 10px",
              borderRadius: 999,
              background: "var(--ink)",
              color: "var(--paper)",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              fontWeight: 700,
            }}
          >
            period <b>7d</b> ×
          </span>
          <span
            style={{
              padding: "4px 10px",
              borderRadius: 999,
              border: "1px dashed var(--ink-3)",
              color: "var(--ink-3)",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              fontWeight: 700,
            }}
          >
            + filter
          </span>
          <span
            className="ticker"
            style={{
              marginLeft: 6,
              fontSize: 16,
              fontWeight: 900,
              letterSpacing: "-0.03em",
            }}
          >
            {visibleCount * 27}
          </span>
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            matches
          </span>
        </div>

        <div
          style={{
            marginTop: 14,
            display: "grid",
            gridTemplateColumns: drawerOpen ? "repeat(3, 1fr) 0px" : "repeat(4, 1fr) 0px",
            gap: 6,
            transition: "grid-template-columns 360ms cubic-bezier(.22,.85,.22,1)",
            paddingRight: drawerOpen ? 220 : 0,
          }}
        >
          {tiles.slice(0, drawerOpen ? 12 : 16).map((c, i) => {
            const shown = i < visibleCount;
            const isHero = i === heroIdx;
            return (
              <div
                key={i}
                style={{
                  aspectRatio: "1/1",
                  background: shown ? c : "transparent",
                  border: shown
                    ? isHero && drawerOpen
                      ? "2px solid var(--bad)"
                      : "1px solid var(--ink)"
                    : "1px dashed var(--ink-4)",
                  position: "relative",
                  opacity: shown ? 1 : 0.3,
                  transform: shown ? "scale(1)" : "scale(0.9)",
                  transition: "all 280ms cubic-bezier(.2,.85,.2,1.1)",
                }}
              >
                {shown && isHero && (
                  <span
                    style={{
                      position: "absolute",
                      top: 4,
                      right: 4,
                      width: 16,
                      height: 16,
                      background: "var(--banana)",
                      border: "1px solid var(--ink)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 10,
                      fontWeight: 900,
                    }}
                  >
                    ★
                  </span>
                )}
                {shown && i === 1 && (
                  <span
                    style={{
                      position: "absolute",
                      top: 4,
                      left: 4,
                      padding: "1px 5px",
                      background: "var(--banana)",
                      color: "var(--ink)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 8,
                      fontWeight: 700,
                    }}
                  >
                    ◐ RUNNING
                  </span>
                )}
                {shown && i === 7 && (
                  <span
                    style={{
                      position: "absolute",
                      top: 4,
                      left: 4,
                      padding: "1px 5px",
                      background: "var(--ink)",
                      color: "var(--paper)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 8,
                      fontWeight: 700,
                    }}
                  >
                    FAIL
                  </span>
                )}
              </div>
            );
          })}
        </div>

        <aside
          style={{
            position: "absolute",
            top: 0,
            right: 0,
            bottom: 0,
            width: 240,
            background: "var(--paper)",
            borderLeft: "1px solid var(--ink)",
            transform: drawerOpen ? "translateX(0)" : "translateX(100%)",
            transition: "transform 360ms cubic-bezier(.22,.85,.22,1)",
            display: "flex",
            flexDirection: "column",
            boxShadow: drawerOpen ? "-8px 0 24px -12px #19171455" : "none",
          }}
        >
          <div
            style={{
              padding: "10px 14px",
              borderBottom: "1px solid var(--ink)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <Mono style={{ fontSize: 8 }}>JOB DETAIL</Mono>
            <span className="display" style={{ fontSize: 13, fontWeight: 800 }}>
              #1424
            </span>
            <span style={{ width: 8, height: 8, background: "var(--ok)" }} />
          </div>
          <div style={{ padding: 12 }}>
            <div
              style={{
                aspectRatio: "16/11",
                background: tiles[heroIdx],
                border: "1px solid var(--ink)",
              }}
            />
            <div style={{ marginTop: 10, display: "flex", gap: 4, flexWrap: "wrap" }}>
              {[["★", "pick"], ["↻", "retry"], ["⌥", "recreate"]].map(([g, l]) => (
                <span
                  key={l}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 3,
                    padding: "3px 7px",
                    border: "1px solid var(--ink)",
                    background: "#fffdf7",
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    fontWeight: 600,
                  }}
                >
                  {g} {l}
                </span>
              ))}
            </div>
            <div style={{ marginTop: 12 }}>
              <Mono style={{ fontSize: 8 }}>PROMPT</Mono>
              <div
                style={{
                  marginTop: 4,
                  padding: "8px 10px",
                  background: "var(--paper-2)",
                  border: "1px solid var(--ink)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 9.5,
                  lineHeight: 1.5,
                }}
              >
                a still-life of three overripe bananas on a marble slab, flemish painting…
              </div>
            </div>
            <div style={{ marginTop: 12, fontFamily: "var(--font-mono)", fontSize: 9 }}>
              {[
                ["model", "Images 2.0"],
                ["shape", "1:1 · 1024×1024"],
                ["duration", "14.2s"],
              ].map(([k, v]) => (
                <div
                  key={k}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    padding: "4px 0",
                    borderBottom: "1px dashed var(--rule-2)",
                  }}
                >
                  <span style={{ color: "var(--ink-3)" }}>{k}</span>
                  <span style={{ fontWeight: 700 }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
        </aside>

        <Cursor x={cursorX} y={cursorY} label={drawerOpen ? "" : "click any tile"} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SCENE 6 — READY
// ---------------------------------------------------------------------------

function SceneReady() {
  return (
    <div style={{ display: "flex", height: "100%" }}>
      <div
        style={{
          flex: 1,
          padding: "44px 44px 36px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
        }}
      >
        <div>
          <Mono>You're set</Mono>
          <h1
            className="display"
            style={{
              fontSize: 64,
              fontWeight: 900,
              letterSpacing: "-0.045em",
              lineHeight: 0.94,
              margin: "18px 0 0",
            }}
          >
            Now go make
            <br />
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
              something
            </span>
            <br />
            ridiculous.
          </h1>
          <p
            style={{
              marginTop: 22,
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 17,
              color: "var(--ink-2)",
              lineHeight: 1.45,
              maxWidth: 360,
            }}
          >
            You can re-open this tour any time from the dashboard's
            "? Replay tour" link.
          </p>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Mono>cheatsheet</Mono>
          <span style={{ flex: 1, height: 1, background: "var(--ink)", opacity: 0.2 }} />
        </div>
      </div>

      <div
        style={{
          flex: 1,
          padding: "44px 44px 36px 12px",
          display: "flex",
          flexDirection: "column",
          gap: 10,
          justifyContent: "center",
        }}
      >
        {[
          ["⌘ N", "blank create"],
          ["⌘ ↵", "generate"],
          ["⌘ [ / ]", "prev / next in drawer"],
          ["★", "pick a keeper"],
          ["↻", "retry job"],
          ["⌥", "recreate w/ edits"],
        ].map(([k, v]) => (
          <div
            key={k}
            style={{
              display: "grid",
              gridTemplateColumns: "92px 1fr",
              alignItems: "center",
              gap: 14,
              padding: "10px 14px",
              background: "#fffdf7",
              border: "1px solid var(--ink)",
              boxShadow: "2px 2px 0 var(--ink)",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                fontWeight: 800,
                background: "var(--ink)",
                color: "var(--banana)",
                padding: "4px 8px",
                textAlign: "center",
              }}
            >
              {k}
            </span>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
