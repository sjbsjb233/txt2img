// MetaRail — 280px right column on the picker page.
// Sections: Outcome / Prompt / Image / In flight / Action footer.

export function MetaRail({ children }) {
  return (
    <div
      style={{
        background: "var(--paper-2)",
        display: "grid",
        gridTemplateRows: "auto auto auto 1fr auto",
        minHeight: 0,
        borderLeft: "1px solid var(--ink)",
      }}
    >
      {children}
    </div>
  );
}

export function RailSection({ title, sub, children, grow }) {
  return (
    <div
      style={{
        padding: "12px 14px",
        borderTop: "1px solid var(--rule-2)",
        ...(grow ? { minHeight: 0, overflowY: "auto" } : {}),
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 8,
        }}
      >
        <span className="caps" style={{ fontSize: 10 }}>
          {title}
        </span>
        {sub && (
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            {sub}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

export function OutcomeBlock({ stats }) {
  const rows = [
    { c: "final", lbl: "Final", n: stats.final || 0 },
    { c: "picked", lbl: "Picked", n: stats.picked || 0 },
    { c: "discarded", lbl: "Discarded", n: stats.discarded || 0 },
    { c: "deferred", lbl: "Deferred", n: stats.deferred || 0 },
    { c: "unjudged", lbl: "Unjudged", n: stats.unjudged || 0 },
  ];
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr auto",
        rowGap: 6,
        columnGap: 8,
        alignItems: "center",
      }}
    >
      {rows.map((r) => (
        <RowFragment key={r.lbl} c={r.c} lbl={r.lbl} n={r.n} />
      ))}
    </div>
  );
}

function RowFragment({ c, lbl, n }) {
  return (
    <>
      <span className={`pk-state-dot ${c}`} />
      <span style={{ fontSize: 12 }}>{lbl}</span>
      <span className="mono" style={{ fontSize: 12, fontWeight: 700 }}>
        {n}
      </span>
    </>
  );
}

export function InFlightCard({ kind, label, value, pct, prompt }) {
  return (
    <div className={`pk-if-card ${kind}`}>
      <div className="head">
        <span>{label}</span>
        <span>{value}</span>
      </div>
      <div className="pk-prog-seg" style={{ height: 4 }}>
        <span
          className="seg-final"
          style={{
            width: `${Math.max(0, Math.min(100, pct || 0))}%`,
            background:
              kind === "failed" ? "var(--bad)" : "var(--banana)",
          }}
        />
        <span className="seg-unjudged" style={{ flex: 1 }} />
      </div>
      {prompt && (
        <div
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            whiteSpace: "nowrap",
            textOverflow: "ellipsis",
            overflow: "hidden",
          }}
        >
          {prompt.slice(0, 60)}{prompt.length > 60 ? "…" : ""}
        </div>
      )}
    </div>
  );
}
