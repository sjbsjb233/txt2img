// Archive empty state — shown when the user is logged in but has no jobs yet.
// Design: "Nano Banana Pro - Empty States" › Archive · zero items (new user)

import { useNavigate } from "react-router-dom";

// Animated diagonal hatch — the visual signature of "no content here".
const HatchSurface = () => (
  <div
    className="arch-empty-hatch"
    style={{
      position: "absolute",
      inset: 0,
      background:
        "repeating-linear-gradient(135deg, var(--paper-2) 0 8px, var(--paper-3) 8px 16px)",
      animation: "emptyDrift 18s linear infinite",
      backgroundSize: "200% 200%",
    }}
  />
);

// Corner tick marks — make the stage feel measured.
const CornerTicks = () =>
  [
    { top: -1, left: -1, borderTop: "2px solid var(--ink)", borderLeft: "2px solid var(--ink)" },
    { top: -1, right: -1, borderTop: "2px solid var(--ink)", borderRight: "2px solid var(--ink)" },
    { bottom: -1, left: -1, borderBottom: "2px solid var(--ink)", borderLeft: "2px solid var(--ink)" },
    { bottom: -1, right: -1, borderBottom: "2px solid var(--ink)", borderRight: "2px solid var(--ink)" },
  ].map((s, i) => (
    <span key={i} style={{ position: "absolute", width: 14, height: 14, ...s }} />
  ));

// Ghost grid — silhouette of work that isn't there yet.
const GhostGrid = () => {
  const cols = 6;
  const rows = 3;
  const cellSize = 52;
  const gap = 6;
  return (
    <div style={{ position: "relative" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, ${cellSize}px)`,
          gridAutoRows: `${cellSize}px`,
          gap,
        }}
      >
        {Array.from({ length: cols * rows }).map((_, i) => (
          <div
            key={i}
            style={{
              border: "1px dashed var(--ink-4)",
              background: "transparent",
              opacity: 0.5 + Math.sin(i * 1.3) * 0.2,
            }}
          />
        ))}
      </div>
      {/* First cell lit in banana — points at what will fill this space */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: cellSize,
          height: cellSize,
          background: "var(--banana)",
          border: "1px solid var(--ink)",
          boxShadow: "3px 3px 0 var(--ink)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "var(--font-display)",
          fontWeight: 900,
          fontSize: 22,
          fontStyle: "italic",
        }}
      >
        1<span style={{ fontSize: 12, fontWeight: 700, opacity: 0.6, marginLeft: 1 }}>st</span>
      </div>
    </div>
  );
};

const FOOTNOTES = [
  {
    n: "01",
    t: "It auto-fills.",
    b: "Every Create run, every batch, every retry. No need to save anything.",
  },
  {
    n: "02",
    t: "Filter the chaos.",
    b: "Slice by model, shape, rating, session. Your filters are URL-shareable.",
  },
  {
    n: "03",
    t: "Picks live forever.",
    b: "Starred work survives cleanup; everything else fades after 90 days.",
  },
];

export default function ArchiveEmptyHero() {
  const navigate = useNavigate();

  return (
    <div style={{ marginTop: 28 }}>
      {/* Big editorial empty stage */}
      <div
        style={{
          position: "relative",
          border: "1px solid var(--ink)",
          background: "#fffdf7",
          minHeight: 540,
          overflow: "hidden",
        }}
      >
        <HatchSurface />
        <CornerTicks />

        {/* Center stack */}
        <div
          style={{
            position: "relative",
            zIndex: 2,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            minHeight: 540,
            padding: "60px 40px",
            gap: 28,
          }}
        >
          <GhostGrid />

          {/* Editorial caption */}
          <div
            style={{
              textAlign: "center",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8,
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.2em" }}
            >
              EMPTY ARCHIVE
            </div>
            <div
              className="display"
              style={{
                fontSize: 22,
                fontStyle: "italic",
                fontWeight: 700,
                letterSpacing: "-0.01em",
                lineHeight: 1.15,
                color: "var(--ink)",
                maxWidth: 460,
              }}
            >
              Every job you run lands here —<br />good ones, bad ones, the lot.
            </div>
            <div
              className="mono"
              style={{
                fontSize: 11,
                color: "var(--ink-3)",
                lineHeight: 1.6,
                maxWidth: 420,
                textAlign: "center",
              }}
            >
              Generations show up the moment they finish. Pick favourites, retry failures,
              batch-export, or just scroll your taste back to yourself.
            </div>
          </div>

          {/* CTA buttons */}
          <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
            <button
              className="btn primary shadowed lg"
              onClick={() => navigate("/create")}
              style={{
                fontFamily: "var(--font-mono)",
                fontWeight: 700,
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                fontSize: 12,
              }}
            >
              Make your first thing <span style={{ marginLeft: 8 }}>›</span>
            </button>
          </div>

          {/* Keyboard hint */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
            <span
              className="mono"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                letterSpacing: "0.16em",
                textTransform: "uppercase",
              }}
            >
              or press
            </span>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: 22,
                height: 22,
                padding: "0 5px",
                border: "1px solid var(--ink-3)",
                background: "#fffdf7",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--ink-2)",
                boxShadow: "0 2px 0 var(--ink-3)",
              }}
            >
              ⌘
            </span>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: 22,
                height: 22,
                padding: "0 5px",
                border: "1px solid var(--ink-3)",
                background: "#fffdf7",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--ink-2)",
                boxShadow: "0 2px 0 var(--ink-3)",
              }}
            >
              N
            </span>
          </div>
        </div>
      </div>

      {/* Footnote grid */}
      <div
        style={{
          marginTop: 22,
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 24,
        }}
      >
        {FOOTNOTES.map((f) => (
          <div key={f.n}>
            <div
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-3)", letterSpacing: "0.16em" }}
            >
              {f.n}
            </div>
            <div
              className="display"
              style={{
                fontSize: 18,
                fontWeight: 800,
                marginTop: 6,
                letterSpacing: "-0.01em",
              }}
            >
              {f.t}
            </div>
            <div
              className="mono"
              style={{
                fontSize: 11,
                color: "var(--ink-3)",
                marginTop: 6,
                lineHeight: 1.6,
              }}
            >
              {f.b}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
