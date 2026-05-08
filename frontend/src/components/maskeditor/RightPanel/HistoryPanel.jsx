export default function HistoryPanel({ items = [], onJump }) {
  return (
    <div data-testid="me-history">
      {items.map((it) => (
        <div
          key={it.index}
          className={
            "me-history-row " +
            (it.active ? "me-history-row--active " : "") +
            (it.ahead ? "me-history-row--ahead" : "")
          }
          onClick={() => onJump?.(it.index)}
        >
          <div className="me-history-thumb" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="me-history-row__title">{it.label}</div>
            <div className="me-history-row__sub">{it.kind}</div>
          </div>
          {it.active && <span className="me-history-row__now">now</span>}
        </div>
      ))}
      {!items.length && (
        <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>
          no history yet — paint to get started.
        </div>
      )}
    </div>
  );
}
