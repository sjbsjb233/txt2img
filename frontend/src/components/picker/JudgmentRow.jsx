// JudgmentRow — five-button action bar at the bottom of the picker.

export default function JudgmentRow({
  disabled = false,
  onPick,
  onDiscard,
  onDefer,
  onFinal,
  onUndo,
}) {
  const dimmed = disabled
    ? { opacity: 0.4, pointerEvents: "none" }
    : {};
  return (
    <div
      style={{
        borderTop: "1px solid var(--ink)",
        padding: "12px 22px",
        background: "var(--paper-soft)",
        display: "flex",
        gap: 12,
        alignItems: "center",
        ...dimmed,
      }}
    >
      <button
        className="pk-judge-btn"
        data-kind="picked"
        style={{ minWidth: 100 }}
        onClick={onPick}
        type="button"
      >
        <div className="glyph">✓</div>
        <div className="lbl">Pick</div>
        <div className="key">P</div>
      </button>
      <button
        className="pk-judge-btn"
        data-kind="discarded"
        style={{ minWidth: 100 }}
        onClick={onDiscard}
        type="button"
      >
        <div className="glyph">✕</div>
        <div className="lbl">Discard</div>
        <div className="key">X</div>
      </button>
      <button
        className="pk-judge-btn"
        style={{ minWidth: 100 }}
        onClick={onDefer}
        type="button"
      >
        <div className="glyph">↺</div>
        <div className="lbl">Defer</div>
        <div className="key">SPACE</div>
      </button>
      <span className="pk-rule-v" />
      <button
        className="pk-judge-btn"
        data-kind="final"
        style={{ minWidth: 140 }}
        onClick={onFinal}
        type="button"
      >
        <div className="glyph">★</div>
        <div className="lbl">Set as FINAL</div>
        <div className="key">F</div>
      </button>
      <div style={{ flex: 1 }} />
      <button
        className="btn sm ghost"
        onClick={onUndo}
        type="button"
      >
        ↶ Undo · <span className="kbd">U</span>
      </button>
    </div>
  );
}
