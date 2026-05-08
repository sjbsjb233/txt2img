import MEIcon from "./MEIcon.jsx";

export default function StatusBar({
  zoom = 100,
  dim = "—",
  cursor = "—",
  brush = 28,
  hint = "",
  onHelp,
}) {
  return (
    <div className="me-statusbar" data-testid="me-statusbar">
      <div className="me-statusbar__cell" style={{ width: 70 }} data-testid="me-statusbar-zoom">
        <b>{zoom}%</b>
      </div>
      <div className="me-statusbar__cell" style={{ width: 120 }} data-testid="me-statusbar-dim">
        {dim}
      </div>
      <div className="me-statusbar__cell" style={{ width: 150 }} data-testid="me-statusbar-cursor">
        cursor {cursor}
      </div>
      <div className="me-statusbar__cell" style={{ width: 120 }} data-testid="me-statusbar-brush">
        brush ø {brush}px
      </div>
      <div className="me-statusbar__cell me-statusbar__cell--hint" data-testid="me-statusbar-hint">
        {hint}
      </div>
      <button
        className="me-statusbar__cell me-statusbar__cell--right"
        style={{ width: 140, border: "none", cursor: "pointer", background: "transparent" }}
        onClick={onHelp}
        title="Keyboard shortcuts"
      >
        <MEIcon name="undo" size={12} /> ⌘Z to undo · <MEIcon name="help" size={12} /> ?
      </button>
    </div>
  );
}
