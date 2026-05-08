import MEIcon from "./MEIcon.jsx";

const COLS = [
  {
    title: "tools",
    rows: [
      ["brush", "B"],
      ["eraser", "E"],
      ["rect mar.", "M"],
      ["lasso", "L"],
      ["magic wand", "W"],
      ["outpaint", "O"],
      ["pan / move", "V/H"],
      ["pan (hold)", "Space"],
    ],
  },
  {
    title: "brush + selection",
    rows: [
      ["size −/+", "[ / ]"],
      ["hardness", "{ / }"],
      ["add", "⇧click"],
      ["subtract", "⌥click"],
      ["select all", "⌘A"],
      ["deselect", "⌘D"],
      ["invert", "⌘I"],
    ],
  },
  {
    title: "view + history",
    rows: [
      ["zoom in/out", "⌘+/⌘−"],
      ["fit", "⌘0"],
      ["100%", "⌘1"],
      ["toggle mask", "M"],
      ["mask only", "⌥M"],
      ["undo / redo", "⌘Z"],
      ["submit", "⌘↵"],
      ["cancel", "Esc"],
    ],
  },
];

export default function CheatSheetOverlay({ onClose }) {
  return (
    <div className="me-cheat-backdrop" onClick={onClose} data-testid="me-cheat">
      <div className="me-cheat" onClick={(e) => e.stopPropagation()}>
        <div className="me-cheat__header">
          <span className="me-cheat__title">keyboard shortcuts</span>
          <span className="kbd" style={{ marginLeft: 8 }}>?</span>
          <button className="btn ghost" onClick={onClose} style={{ marginLeft: "auto" }}>
            <MEIcon name="close" size={14} />
          </button>
        </div>
        <div className="me-cheat__cols">
          {COLS.map((col, i) => (
            <div key={i} className="me-cheat__col">
              <div className="me-cheat__col-title">{col.title}</div>
              {col.rows.map(([label, key]) => (
                <div key={label} className="me-cheat__row">
                  <span>{label}</span>
                  <span className="me-kbd">{key}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
