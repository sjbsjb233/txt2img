export default function StreamingOverlay({ progress = 0, label = "generating", elapsed = 0, partial = "", onCancel }) {
  return (
    <div className="me-streaming-bar" data-testid="me-streaming">
      <div className="me-spinner" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, fontWeight: 600 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>
            {Math.round(elapsed)}s {partial && `· ${partial}`}
          </span>
        </div>
        <div className="me-progress">
          <div className="me-progress__fill" style={{ width: `${Math.max(0, Math.min(100, progress * 100))}%` }} />
        </div>
      </div>
      {onCancel && (
        <button className="btn sm" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={onCancel}>
          cancel
        </button>
      )}
    </div>
  );
}
