import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import SizeCustomModal from "../src/components/SizeCustomModal.jsx";
import "../src/styles/tokens.css";

function Harness() {
  const params = new URLSearchParams(window.location.search);
  const initial = params.get("initial");
  const [open, setOpen] = useState(true);
  const [selected, setSelected] = useState(initial || null);

  return (
    <div style={{ minHeight: "100vh", background: "var(--paper)" }}>
      <div style={{ padding: 24 }}>
        <button
          data-testid="harness-open"
          className="btn"
          onClick={() => setOpen(true)}
        >
          Open modal
        </button>
        <span
          data-testid="harness-selected"
          className="mono"
          style={{ marginLeft: 12, fontSize: 12 }}
        >
          selected: {selected || "(none)"}
        </span>
      </div>
      <SizeCustomModal
        open={open}
        initialValue={selected}
        onClose={() => setOpen(false)}
        onSelect={(s) => {
          setSelected(s);
          setOpen(false);
        }}
      />
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Harness />
  </StrictMode>
);
