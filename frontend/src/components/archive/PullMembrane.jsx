// "GoodNotes" style pull-to-load membrane. Renders the tension zone at
// the bottom of the loaded grid; the parent owns the `pull` 0..1 value.

import { TENSION_HEIGHT } from "../../hooks/useArchivePagination.js";

export default function PullMembrane({
  pull,
  isCommitting,
  nextPage,
  visible,
}) {
  if (!visible) return null;
  const clamped = Math.max(0, Math.min(1.4, pull || 0));
  let hint;
  if (isCommitting) hint = "LOADING...";
  else if (clamped >= 1) hint = `RELEASE TO LOAD PAGE ${nextPage}`;
  else if (clamped >= 0.7) hint = "ALMOST THERE...";
  else if (clamped >= 0.3) hint = "KEEP PULLING ↓";
  else hint = `PULL TO LOAD PAGE ${nextPage}`;

  const accentColor = clamped >= 1 ? "var(--ink)" : "var(--ink-3)";
  const bowY = 20 + clamped * 160;

  return (
    <div
      data-testid="archive-tension"
      data-pull={clamped.toFixed(2)}
      style={{
        height: TENSION_HEIGHT,
        background: "var(--paper-2)",
        borderTop: "1px dashed var(--ink-4)",
        position: "relative",
        overflow: "hidden",
        display: visible ? "flex" : "none",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <svg
        width="600"
        height="200"
        viewBox="0 0 600 200"
        style={{
          maxWidth: "92%",
          opacity: isCommitting ? 0 : 1,
          transition: "opacity 220ms ease",
        }}
      >
        <line
          x1="0"
          y1="20"
          x2="600"
          y2="20"
          stroke="var(--ink-3)"
          strokeWidth="1"
          strokeDasharray="4 4"
        />
        <path
          data-testid="membrane-bow"
          d={`M 0 20 Q 300 ${bowY} 600 20`}
          fill="none"
          stroke={accentColor}
          strokeWidth={clamped >= 1 ? 2.5 : 1.5}
        />
        <text
          data-testid="membrane-hint"
          x="300"
          y={Math.min(bowY + 30, 190)}
          fill={clamped >= 1 ? "var(--ink)" : "var(--ink-3)"}
          fontFamily="var(--font-mono)"
          fontSize="11"
          textAnchor="middle"
          letterSpacing="0.18em"
          fontWeight={clamped >= 1 ? 700 : 500}
        >
          {hint}
        </text>
      </svg>
    </div>
  );
}
