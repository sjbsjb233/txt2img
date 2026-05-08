import MEIcon from "./MEIcon.jsx";

const SECTIONS = [
  [{ id: "pan", icon: "pan", label: "Pan / Move", hotkey: "V" }],
  [
    { id: "brush", icon: "brush", label: "Brush", hotkey: "B" },
    { id: "eraser", icon: "eraser", label: "Eraser", hotkey: "E" },
  ],
  [
    { id: "rect", icon: "rect-marquee", label: "Rectangle marquee", hotkey: "M" },
    { id: "lasso", icon: "lasso", label: "Lasso", hotkey: "L" },
    { id: "wand", icon: "wand", label: "Magic wand", hotkey: "W" },
  ],
  [{ id: "outpaint", icon: "outpaint", label: "Outpaint mode", hotkey: "O" }],
];

function ToolBtn({ id, icon, label, hotkey, active, onClick }) {
  return (
    <button
      className={`me-tool-btn ${active ? "me-tool-btn--active" : ""}`}
      onClick={onClick}
      title={`${label} (${hotkey})`}
      data-testid={`me-tool-${id}`}
    >
      <MEIcon name={icon} size={18} />
      <span className="me-tool-btn__hotkey">{hotkey}</span>
    </button>
  );
}

export default function Toolbar({ active, onPick, onUndo, onRedo, canUndo, canRedo }) {
  return (
    <div className="me-toolbar" data-testid="me-toolbar">
      {SECTIONS.map((section, i) => (
        <div key={i}>
          {section.map((tool) => (
            <ToolBtn
              key={tool.id}
              {...tool}
              active={active === tool.id}
              onClick={() => onPick?.(tool.id)}
            />
          ))}
          {i < SECTIONS.length - 1 && <div className="me-tool-sep" />}
        </div>
      ))}
      <div className="me-tool-spacer" />
      <ToolBtn
        id="undo"
        icon="undo"
        label="Undo"
        hotkey="⌘Z"
        onClick={onUndo}
        active={false}
      />
      <ToolBtn
        id="redo"
        icon="redo"
        label="Redo"
        hotkey="⇧⌘Z"
        onClick={onRedo}
        active={false}
      />
    </div>
  );
}
