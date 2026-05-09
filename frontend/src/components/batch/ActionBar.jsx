import Icon from "../Icon.jsx";

export default function ActionBar({
  slotCount,
  imageCount,
  batchesInFlight,
  K,
  locked,
  submitting,
  submittingProgress,
  onValidate,
  onSubmit,
}) {
  const submitDisabled = locked || imageCount === 0 || submitting;
  return (
    <div
      data-testid="action-bar"
      style={{
        borderTop: "2px solid var(--ink)",
        background: "var(--ink)",
        color: "var(--paper)",
        padding: "12px 24px",
        display: "flex",
        alignItems: "center",
        gap: 18,
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 14,
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
          <span
            style={{
              fontSize: 28,
              fontWeight: 900,
              letterSpacing: "-0.03em",
              fontFamily: "var(--font-display)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {slotCount}
          </span>
          <span
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-4)" }}
          >
            slots
          </span>
        </div>
        <span style={{ color: "var(--ink-3)", fontSize: 14 }}>·</span>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
          <span
            style={{
              fontSize: 28,
              fontWeight: 900,
              letterSpacing: "-0.03em",
              fontFamily: "var(--font-display)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {imageCount}
          </span>
          <span
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-4)" }}
          >
            images
          </span>
        </div>
        <span style={{ color: "var(--ink-3)", fontSize: 14 }}>·</span>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
          <span
            data-testid="batches-in-flight"
            style={{
              fontSize: 28,
              fontWeight: 900,
              letterSpacing: "-0.03em",
              color: locked ? "var(--bad)" : "var(--banana)",
              fontFamily: "var(--font-display)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {batchesInFlight}/{K}
          </span>
          <span
            className="mono caps"
            style={{
              fontSize: 10,
              color: locked ? "#ffb0a3" : "var(--ink-4)",
            }}
          >
            batches in flight
          </span>
        </div>
      </div>

      <span
        style={{ width: 1, height: 28, background: "#ffffff20" }}
      />

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flex: 1,
          minWidth: 0,
        }}
      >
        {submitting ? (
          <>
            <span
              className="chip"
              style={{
                background: "var(--banana)",
                borderColor: "var(--banana)",
                color: "var(--ink)",
                border: "1px solid var(--banana)",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                padding: "2px 8px",
                fontWeight: 700,
              }}
              data-testid="action-bar-status"
            >
              SUBMITTING {submittingProgress?.done || 0}/
              {submittingProgress?.total || 0} JOBS…
            </span>
            <span
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-4)" }}
            >
              editor cleared · progress on top card · POST /api/jobs (
              {submittingProgress?.done || 0} of{" "}
              {submittingProgress?.total || 0})
            </span>
          </>
        ) : locked ? (
          <>
            <span
              className="chip"
              style={{
                background: "var(--bad)",
                borderColor: "var(--bad)",
                color: "white",
                border: "1px solid var(--bad)",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                padding: "2px 8px",
                fontWeight: 700,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
              data-testid="action-bar-status"
            >
              <Icon name="close" size={9} /> BATCH LIMIT
            </span>
            <span
              className="mono"
              style={{ fontSize: 11, color: "#ffb0a3" }}
            >
              already running {K} batches · let one finish or cancel before
              starting another
            </span>
          </>
        ) : (
          <>
            <span
              className="chip"
              style={{
                background: "transparent",
                color: "var(--paper)",
                borderColor: "var(--paper)",
                border: "1px solid var(--paper)",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                padding: "2px 8px",
                fontWeight: 700,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
              data-testid="action-bar-status"
            >
              <Icon name="check" size={9} /> DRAFT SAVED
            </span>
            <span
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-4)" }}
            >
              local-only · IndexedDB · this device only · clears on submit
            </span>
          </>
        )}
      </div>

      <span
        style={{ width: 1, height: 28, background: "#ffffff20" }}
      />

      <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
        <button
          type="button"
          onClick={onValidate}
          className="btn ghost"
          style={{
            background: "transparent",
            color: "var(--paper)",
            border: "1px solid var(--paper)",
            cursor: "pointer",
            padding: "0 16px",
            height: 38,
            fontWeight: 600,
            fontSize: 13,
          }}
        >
          Validate
        </button>
        <button
          type="button"
          onClick={onSubmit}
          disabled={submitDisabled}
          data-testid="submit-batch"
          style={{
            minWidth: 220,
            height: 48,
            padding: "0 22px",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            background: submitDisabled ? "#ffffff15" : "var(--banana)",
            border: `1px solid ${
              submitDisabled ? "#ffffff30" : "var(--ink)"
            }`,
            color: submitDisabled ? "#ffffff60" : "var(--ink)",
            cursor: submitDisabled ? "not-allowed" : "pointer",
            fontWeight: 700,
            fontSize: 14,
            boxShadow: submitDisabled ? "none" : "3px 3px 0 var(--paper)",
          }}
        >
          <Icon name="bolt" size={14} />
          {submitDisabled && locked
            ? "Submit batch · LOCKED"
            : `Submit batch · ${imageCount} jobs`}
        </button>
      </div>
    </div>
  );
}
