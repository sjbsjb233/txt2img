export function Logo({ size = 32 }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        background: "var(--banana)",
        border: "1px solid var(--ink)",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: size * 0.42,
          fontWeight: 700,
          color: "var(--ink)",
          letterSpacing: "-0.04em",
          lineHeight: 1,
        }}
      >
        t2i
      </span>
      <span
        style={{
          position: "absolute",
          bottom: -2,
          right: -2,
          width: 6,
          height: 6,
          background: "var(--ink)",
        }}
      />
    </div>
  );
}

export function Wordmark({ big = false }) {
  return (
    <div style={{ display: "inline-flex", alignItems: "baseline", gap: 6 }}>
      <span
        className="display"
        style={{
          fontSize: big ? 28 : 18,
          fontWeight: 900,
          letterSpacing: "-0.04em",
          color: "var(--ink)",
          lineHeight: 1,
        }}
      >
        txt
        <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>2</span>
        img
      </span>
      <span
        className="mono caps"
        style={{ fontSize: big ? 11 : 9, color: "var(--ink-3)" }}
      >
        /studio
      </span>
    </div>
  );
}
