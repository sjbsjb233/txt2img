const STRATEGIES = [
  {
    id: "per_slot_new",
    label: "Per-slot · new",
    note: "1 session per slot",
  },
  {
    id: "batch_shared_new",
    label: "Batch shared · new",
    note: "1 session for all",
  },
  {
    id: "existing_session",
    label: "Existing session",
    note: "pick from picker",
  },
  {
    id: "none",
    label: "No session",
    note: "skip picker; archive only",
  },
];

const DEFAULT_IMAGE_PRESETS = [1, 2, 4, 8];

export default function RightRail({
  sessionStrategy,
  defaultImageCount,
  onSessionStrategyChange,
  onDefaultImageCountChange,
}) {
  return (
    <div
      style={{
        background: "var(--paper-2)",
        borderLeft: "1px solid var(--ink)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: "20px 22px",
        }}
      >
        <div
          className="mono caps"
          style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 8 }}
        >
          Session strategy
        </div>
        <div
          style={{ display: "flex", flexDirection: "column", gap: 4 }}
          data-testid="session-strategy"
        >
          {STRATEGIES.map((s) => {
            const on = sessionStrategy === s.id;
            return (
              <button
                type="button"
                key={s.id}
                onClick={() => onSessionStrategyChange?.(s.id)}
                data-testid={`session-strategy-${s.id}`}
                style={{
                  padding: "9px 11px",
                  textAlign: "left",
                  border: "1px solid var(--ink)",
                  background: on ? "var(--ink)" : "#fffdf7",
                  color: on ? "var(--paper)" : "var(--ink)",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                }}
              >
                <span
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: 12,
                    fontWeight: 700,
                  }}
                >
                  <span
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      border: `1px solid ${
                        on ? "var(--banana)" : "var(--ink)"
                      }`,
                      background: on ? "var(--banana)" : "transparent",
                      display: "inline-block",
                    }}
                  />
                  {s.label}
                </span>
                <span
                  className="mono"
                  style={{
                    fontSize: 9,
                    color: on ? "var(--paper-2)" : "var(--ink-3)",
                    paddingLeft: 16,
                  }}
                >
                  {s.note}
                </span>
              </button>
            );
          })}
        </div>

        <div
          className="hair"
          style={{ margin: "18px 0", height: 1, background: "var(--ink)", opacity: 0.12 }}
        />

        <div
          className="mono caps"
          style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 8 }}
        >
          Default image count
        </div>
        <div style={{ display: "flex", border: "1px solid var(--ink)" }}>
          {DEFAULT_IMAGE_PRESETS.map((n, i) => {
            const on = defaultImageCount === n;
            return (
              <button
                type="button"
                key={n}
                onClick={() => onDefaultImageCountChange?.(n)}
                data-testid={`default-image-${n}`}
                style={{
                  flex: 1,
                  height: 28,
                  background: on ? "var(--banana)" : "#fffdf7",
                  border: "none",
                  borderLeft: i ? "1px solid var(--ink)" : "none",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                {n}
              </button>
            );
          })}
        </div>
        <div
          className="mono"
          style={{
            fontSize: 9,
            color: "var(--ink-4)",
            marginTop: 6,
            lineHeight: 1.4,
          }}
        >
          new slots use this. hard cap 16/slot · 400 total.
        </div>

        <div
          className="hair"
          style={{ margin: "18px 0", height: 1, background: "var(--ink)", opacity: 0.12 }}
        />

        <div
          className="mono caps"
          style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 8 }}
        >
          Recovery
        </div>
        <div
          style={{
            padding: "10px 12px",
            border: "1px solid var(--ink-3)",
            background: "#fffdf7",
          }}
        >
          <div
            className="mono"
            style={{
              fontSize: 10,
              color: "var(--ink-2)",
              lineHeight: 1.5,
            }}
          >
            server-side watchdog (60s)
            <br />
            <span style={{ color: "var(--ink-3)" }}>
              auto-marks abandoned batches.
              <br />
              no client-side resume.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
