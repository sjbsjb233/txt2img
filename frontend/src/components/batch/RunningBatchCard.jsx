// Status → card chrome (frontend doc v0.3 §2.1).
const STATUS_TONE = {
  submitting: {
    bg: "#fdf2d6",
    ring: "var(--ink)",
    dot: "var(--banana-deep)",
    label: "SUBMITTING",
  },
  running: {
    bg: "var(--banana-soft)",
    ring: "var(--ink)",
    dot: "var(--banana-deep)",
    label: "RUNNING",
  },
  completed: {
    bg: "#d8eedf",
    ring: "var(--ink)",
    dot: "var(--ok)",
    label: "COMPLETED",
  },
  partial: {
    bg: "#fdf2d6",
    ring: "var(--bad)",
    dot: "var(--bad)",
    label: "PARTIAL · FAILED",
  },
  cancelled: {
    bg: "var(--paper-3)",
    ring: "var(--ink)",
    dot: "var(--ink-3)",
    label: "CANCELLED",
  },
  abandoned: {
    bg: "var(--paper-3)",
    ring: "var(--ink-3)",
    dot: "var(--ink-3)",
    label: "ABANDONED ⚠",
  },
};

function relativeTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  return `${d}d ago`;
}

const MAIN_BTN = {
  submitting: "Open",
  running: "Open",
  completed: "Open in Picker →",
  partial: "Open",
  cancelled: "Open",
  abandoned: "Open",
};

const SECONDARY_BTN = {
  submitting: null,
  running: "Cancel queued",
  completed: "Dismiss",
  partial: "Retry failed",
  cancelled: "Dismiss",
  abandoned: "Discard",
};

export default function RunningBatchCard({ b, onOpen, onSecondary }) {
  const tone = STATUS_TONE[b.status] || STATUS_TONE.running;
  const total = b.total_job_count || 0;
  const succ = b.succeeded_count || 0;
  const fail = b.failed_count || 0;
  const flight = b.in_flight_count || 0;
  const cancelled = b.cancelled_count || 0;
  const queued = Math.max(0, total - succ - fail - flight - cancelled);
  const succPct = total ? (succ / total) * 100 : 0;
  const flightPct = total ? (flight / total) * 100 : 0;
  const failPct = total ? (fail / total) * 100 : 0;

  const mainBtn = MAIN_BTN[b.status] || "Open";
  const secondaryBtn = SECONDARY_BTN[b.status];
  const updatedAt = b.updated_at || b.last_activity_at || b.created_at;

  return (
    <div
      data-testid="batch-card"
      data-status={b.status}
      data-batch-id={b.batch_id}
      style={{
        flexShrink: 0,
        width: 232,
        border: `${b.status === "partial" ? 2 : 1}px solid ${tone.ring}`,
        background: tone.bg,
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}
    >
      <div
        style={{
          padding: "8px 10px 6px",
          display: "flex",
          alignItems: "center",
          gap: 6,
          borderBottom: "1px solid var(--rule-2)",
        }}
      >
        <span
          style={{
            width: 7,
            height: 7,
            background: tone.dot,
            borderRadius:
              b.status === "running" || b.status === "submitting"
                ? 0
                : "50%",
            flexShrink: 0,
          }}
        />
        <span
          className="mono caps"
          style={{
            fontSize: 9,
            color: "var(--ink-2)",
            letterSpacing: "0.06em",
            fontWeight: 700,
          }}
        >
          {tone.label}
        </span>
        <span style={{ flex: 1 }} />
        <span
          className="mono"
          style={{ fontSize: 9, color: "var(--ink-4)" }}
        >
          {relativeTime(updatedAt)}
        </span>
      </div>

      <div style={{ padding: "8px 10px 4px" }}>
        <div
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 14,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            color: "var(--ink)",
            lineHeight: 1.15,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {b.title || "Untitled batch"}
        </div>
        <div
          className="mono"
          style={{
            fontSize: 8,
            color: "var(--ink-4)",
            marginTop: 2,
            letterSpacing: "0.04em",
          }}
        >
          {b.batch_id}
        </div>
      </div>

      <div
        style={{
          padding: "0 10px 6px",
          display: "flex",
          alignItems: "baseline",
          gap: 6,
        }}
      >
        <span
          className="ticker"
          style={{
            fontSize: 22,
            fontWeight: 900,
            letterSpacing: "-0.04em",
            color:
              b.status === "abandoned" ? "var(--ink-3)" : "var(--ink)",
            fontFamily: "var(--font-display)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {succ}/{total}
        </span>
        {fail > 0 && (
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--bad)", fontWeight: 700 }}
          >
            · {fail} fail
          </span>
        )}
        {flight > 0 && (
          <span
            className="mono"
            style={{
              fontSize: 10,
              color: "var(--banana-deep)",
              fontWeight: 700,
            }}
          >
            · {flight} flight
          </span>
        )}
        {queued > 0 &&
          b.status !== "abandoned" &&
          b.status !== "cancelled" && (
            <span
              className="mono"
              style={{ fontSize: 9, color: "var(--ink-4)" }}
            >
              · {queued} q
            </span>
          )}
      </div>

      <div
        style={{
          margin: "0 10px 8px",
          height: 6,
          background: "var(--paper-3)",
          border: "1px solid var(--ink)",
          display: "flex",
          overflow: "hidden",
        }}
      >
        <span
          style={{ width: `${succPct}%`, background: "var(--ok)" }}
        />
        <span
          style={{
            width: `${flightPct}%`,
            background:
              "repeating-linear-gradient(45deg, var(--banana) 0 4px, var(--banana-deep) 4px 8px)",
          }}
        />
        <span
          style={{ width: `${failPct}%`, background: "var(--bad)" }}
        />
      </div>

      <div
        style={{
          padding: "0 8px 8px",
          display: "flex",
          gap: 4,
        }}
      >
        <button
          onClick={() => onOpen?.(b.batch_id)}
          className="btn sm"
          style={{
            flex: 1,
            background:
              b.status === "completed" ? "var(--banana)" : "#fffdf7",
            fontSize: 11,
            padding: "5px 8px",
            justifyContent: "center",
            minWidth: 0,
            border: "1px solid var(--ink)",
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          {mainBtn}
        </button>
        {secondaryBtn && (
          <button
            onClick={() => onSecondary?.(b.batch_id, b.status)}
            className="btn sm ghost"
            style={{
              fontSize: 10,
              padding: "5px 8px",
              background: "transparent",
              border: "1px solid var(--ink)",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            {secondaryBtn}
          </button>
        )}
      </div>
    </div>
  );
}

export { STATUS_TONE };
