// Shared atoms used across admin tabs. Kept local to /pages/admin/ so the
// admin module is the only consumer (these aren't general-purpose UI yet).

export function StatusDot({ tone = "ok", label }) {
  const palette = {
    ok: { bg: "var(--ok)", text: "var(--ok)" },
    warn: { bg: "var(--warn)", text: "var(--warn)" },
    bad: { bg: "var(--bad)", text: "var(--bad)" },
    info: { bg: "var(--info)", text: "var(--info)" },
    muted: { bg: "var(--ink-4)", text: "var(--ink-3)" },
  }[tone];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          width: 8,
          height: 8,
          background: palette.bg,
          borderRadius: "50%",
          flexShrink: 0,
        }}
      />
      <span
        className="mono caps"
        style={{ fontSize: 10, color: palette.text, fontWeight: 700 }}
      >
        {label}
      </span>
    </span>
  );
}

export function TierPill({ tier }) {
  const map = {
    vip: { bg: "var(--banana)", color: "var(--ink)", label: "VIP" },
    premium: { bg: "var(--ink)", color: "var(--banana)", label: "PREMIUM" },
    standard: { bg: "var(--paper-3)", color: "var(--ink)", label: "STANDARD" },
    free: {
      bg: "transparent",
      color: "var(--ink-3)",
      label: "FREE",
      border: "1px solid var(--ink-4)",
    },
  }[tier] || { bg: "var(--paper-3)", color: "var(--ink)", label: tier?.toUpperCase() };
  return (
    <span
      className="mono"
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 7px",
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.08em",
        background: map.bg,
        color: map.color,
        border: map.border || "1px solid var(--ink)",
      }}
    >
      {map.label}
    </span>
  );
}

export function Hair({ thick }) {
  return (
    <div
      style={{
        height: thick ? 2 : 1,
        background: "var(--ink)",
        opacity: thick ? 1 : 0.18,
      }}
    />
  );
}
