// Outpaint-tool panel (direction + amount + preview).
//
// Used inside the "outpaint" tab of the three-tab right rail (see PRD
// §5.8). Prompt input lives in the separate ``prompt`` tab so the
// inpaint and outpaint surfaces share the same PromptPanel.

import { Field, Seg } from "./atoms.jsx";

const DIRECTIONS = ["top", "right", "bottom", "left"];

export default function OutpaintToolPanel({
  outpaint,
  setOutpaint,
  imageW,
  imageH,
}) {
  function toggleDir(dir) {
    const set = new Set(outpaint.directions);
    if (set.has(dir)) set.delete(dir);
    else set.add(dir);
    setOutpaint({ ...outpaint, directions: Array.from(set) });
  }

  const amount = parseAmount(outpaint.amount, imageW, imageH);
  const newW =
    imageW +
    (outpaint.directions.includes("left") ? amount.x : 0) +
    (outpaint.directions.includes("right") ? amount.x : 0);
  const newH =
    imageH +
    (outpaint.directions.includes("top") ? amount.y : 0) +
    (outpaint.directions.includes("bottom") ? amount.y : 0);

  return (
    <div
      className="me-panel-body"
      data-testid="me-outpaint-panel"
      style={{ flex: 1, overflow: "auto" }}
    >
      <Field label="extend direction">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "40px 1fr 40px",
            gridTemplateRows: "40px 80px 40px",
            gap: 4,
            alignItems: "center",
            justifyItems: "center",
          }}
        >
          <div />
          <DirBtn
            label="↑"
            active={outpaint.directions.includes("top")}
            onClick={() => toggleDir("top")}
          />
          <div />
          <DirBtn
            label="←"
            active={outpaint.directions.includes("left")}
            onClick={() => toggleDir("left")}
          />
          <div
            style={{
              width: 80,
              height: 64,
              border: "1px solid var(--ink)",
              background: "#1f1812",
            }}
          />
          <DirBtn
            label="→"
            active={outpaint.directions.includes("right")}
            onClick={() => toggleDir("right")}
          />
          <div />
          <DirBtn
            label="↓"
            active={outpaint.directions.includes("bottom")}
            onClick={() => toggleDir("bottom")}
          />
          <div />
        </div>
      </Field>

      <Field label="extend amount">
        <Seg
          options={["10%", "25%", "50%", "100%", "custom"]}
          value={
            outpaint.amount === "10%" ||
            outpaint.amount === "25%" ||
            outpaint.amount === "50%" ||
            outpaint.amount === "100%"
              ? outpaint.amount
              : "custom"
          }
          onChange={(v) => {
            if (v === "custom") setOutpaint({ ...outpaint, amount: outpaint.amount || "256px" });
            else setOutpaint({ ...outpaint, amount: v });
          }}
          dense
        />
        {outpaint.amount &&
          !["10%", "25%", "50%", "100%"].includes(outpaint.amount) && (
            <input
              className="inp"
              value={outpaint.amount}
              onChange={(e) => setOutpaint({ ...outpaint, amount: e.target.value })}
              style={{ marginTop: 6, height: 28, fontSize: 11 }}
              placeholder="256px or 25%"
            />
          )}
      </Field>

      <div
        style={{
          background: "var(--paper-2)",
          border: "1px solid var(--rule)",
          padding: 10,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          lineHeight: 1.5,
          marginBottom: 14,
        }}
      >
        <div>
          new canvas: {newW} × {newH}
        </div>
        <div>
          original: {imageW} × {imageH}
        </div>
        <div style={{ color: "var(--bad)", fontWeight: 700 }}>
          extended: {outpaint.directions.length === 0
            ? "(no direction selected)"
            : `+${amount.x} px ${outpaint.directions.join(" + ")}`}
        </div>
      </div>
    </div>
  );
}

function DirBtn({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        width: 40,
        height: 40,
        padding: 0,
        background: active ? "var(--banana)" : "#fffdf7",
        boxShadow: active ? "2px 2px 0 var(--ink)" : "none",
        border: "1px solid var(--ink)",
        fontSize: 18,
        fontWeight: 700,
        cursor: "pointer",
        color: "var(--ink)",
        fontFamily: "var(--font-sans)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {label}
    </button>
  );
}

function parseAmount(amount, w, h) {
  if (!amount) return { x: 0, y: 0 };
  // Guard parseInt against invalid input like "abcpx" / "%%" that would
  // otherwise propagate NaN into newW/newH and the preview text.
  if (amount.endsWith("%")) {
    const raw = parseInt(amount.slice(0, -1), 10);
    if (!Number.isFinite(raw)) return { x: 0, y: 0 };
    const p = raw / 100;
    return { x: Math.round(w * p), y: Math.round(h * p) };
  }
  if (amount.endsWith("px")) {
    const raw = parseInt(amount.slice(0, -2), 10);
    if (!Number.isFinite(raw)) return { x: 0, y: 0 };
    return { x: raw, y: raw };
  }
  return { x: 0, y: 0 };
}

export { DIRECTIONS };
