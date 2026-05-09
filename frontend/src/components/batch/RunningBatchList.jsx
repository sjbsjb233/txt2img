import RunningBatchCard from "./RunningBatchCard.jsx";

export default function RunningBatchList({
  batches,
  onOpen,
  onShowAll,
  onSecondary,
}) {
  return (
    <div
      data-testid="running-batch-list"
      style={{
        borderBottom: "1px solid var(--ink)",
        background: "var(--paper-2)",
        padding: "16px 28px 18px",
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span
            className="display"
            style={{
              fontSize: 18,
              fontWeight: 800,
              letterSpacing: "-0.025em",
              fontFamily: "var(--font-display)",
            }}
          >
            Running batches
          </span>
          <span
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)" }}
          >
            server truth · cross-device · sse live
          </span>
        </div>
        <div
          style={{ display: "flex", alignItems: "center", gap: 10 }}
        >
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            {batches.length} visible
          </span>
          {onShowAll && (
            <button
              onClick={onShowAll}
              className="chip"
              style={{
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                padding: "2px 8px",
                cursor: "pointer",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
              }}
            >
              SHOW ALL ›
            </button>
          )}
        </div>
      </div>

      <div
        style={{
          display: "flex",
          gap: 10,
          overflowX: "auto",
          paddingBottom: 4,
        }}
      >
        {batches.length === 0 && (
          <div
            data-testid="batch-empty"
            style={{
              flex: 1,
              padding: "20px 12px",
              border: "1px dashed var(--ink-3)",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--ink-4)",
              textAlign: "center",
              lineHeight: 1.5,
            }}
          >
            No running batches yet — submit one below.
          </div>
        )}
        {batches.map((b) => (
          <RunningBatchCard
            key={b.batch_id}
            b={b}
            onOpen={onOpen}
            onSecondary={onSecondary}
          />
        ))}
        {batches.length > 0 && (
          <div
            style={{
              flexShrink: 0,
              width: 96,
              alignSelf: "stretch",
              border: "1px dashed var(--ink-3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--ink-4)",
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              textAlign: "center",
              lineHeight: 1.3,
              padding: "0 8px",
            }}
          >
            new batches
            <br />
            insert here →
          </div>
        )}
      </div>
    </div>
  );
}
