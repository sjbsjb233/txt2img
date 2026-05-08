import { Field, FieldLabel, Seg } from "./atoms.jsx";
import { SectionHeader } from "./atoms.jsx";

const DIRECTIONS = ["top", "right", "bottom", "left"];

export default function OutpaintRightPanel({
  outpaint,
  setOutpaint,
  imageW,
  imageH,
  prompt,
  setPrompt,
}) {
  function toggleDir(dir) {
    const set = new Set(outpaint.directions);
    if (set.has(dir)) set.delete(dir);
    else set.add(dir);
    setOutpaint({ ...outpaint, directions: Array.from(set) });
  }

  // Compute new canvas size based on amount.
  const amount = parseAmount(outpaint.amount, imageW, imageH);
  const newW = imageW + (outpaint.directions.includes("left") ? amount.x : 0) + (outpaint.directions.includes("right") ? amount.x : 0);
  const newH = imageH + (outpaint.directions.includes("top") ? amount.y : 0) + (outpaint.directions.includes("bottom") ? amount.y : 0);

  return (
    <>
      <SectionHeader>outpaint</SectionHeader>
      <div className="me-panel-body" data-testid="me-outpaint-panel" style={{ flex: 1, overflow: "auto" }}>
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
            <DirBtn label="↑" active={outpaint.directions.includes("top")} onClick={() => toggleDir("top")} />
            <div />
            <DirBtn label="←" active={outpaint.directions.includes("left")} onClick={() => toggleDir("left")} />
            <div style={{
              width: 80, height: 64,
              border: "1px solid var(--ink)",
              background: "#1f1812",
            }} />
            <DirBtn label="→" active={outpaint.directions.includes("right")} onClick={() => toggleDir("right")} />
            <div />
            <DirBtn label="↓" active={outpaint.directions.includes("bottom")} onClick={() => toggleDir("bottom")} />
            <div />
          </div>
        </Field>

        <Field label="extend amount">
          <Seg
            options={["10%", "25%", "50%", "100%", "custom"]}
            value={outpaint.amount === "10%" || outpaint.amount === "25%" || outpaint.amount === "50%" || outpaint.amount === "100%" ? outpaint.amount : "custom"}
            onChange={(v) => {
              if (v === "custom") setOutpaint({ ...outpaint, amount: outpaint.amount || "256px" });
              else setOutpaint({ ...outpaint, amount: v });
            }}
            dense
          />
          {outpaint.amount && !["10%", "25%", "50%", "100%"].includes(outpaint.amount) && (
            <input
              className="inp"
              value={outpaint.amount}
              onChange={(e) => setOutpaint({ ...outpaint, amount: e.target.value })}
              style={{ marginTop: 6, height: 28, fontSize: 11 }}
              placeholder="256px or 25%"
            />
          )}
        </Field>

        <div style={{
          background: "var(--paper-2)",
          border: "1px solid var(--rule)",
          padding: 10,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          lineHeight: 1.5,
          marginBottom: 14,
        }}>
          <div>new canvas: {newW} × {newH}</div>
          <div>original: {imageW} × {imageH}</div>
          <div style={{ color: "var(--bad)", fontWeight: 700 }}>
            extended: {outpaint.directions.length === 0 ? "(no direction selected)" : `+${amount.x} px ${outpaint.directions.join(" + ")}`}
          </div>
        </div>

        <Field label="prompt — describe the new area">
          <textarea
            className="inp"
            rows={4}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            style={{ resize: "none", lineHeight: 1.5 }}
            placeholder="describe lighting, palette, surroundings…"
            data-testid="me-outpaint-prompt"
          />
        </Field>
      </div>
    </>
  );
}

function DirBtn({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        width: 40, height: 40, padding: 0,
        background: active ? "var(--banana)" : "#fffdf7",
        boxShadow: active ? "2px 2px 0 var(--ink)" : "none",
        border: "1px solid var(--ink)",
        fontSize: 18, fontWeight: 700,
        cursor: "pointer",
        color: "var(--ink)",
        fontFamily: "var(--font-sans)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      {label}
    </button>
  );
}

function parseAmount(amount, w, h) {
  if (!amount) return { x: 0, y: 0 };
  if (amount.endsWith("%")) {
    const p = parseInt(amount.slice(0, -1), 10) / 100;
    return { x: Math.round(w * p), y: Math.round(h * p) };
  }
  if (amount.endsWith("px")) {
    const p = parseInt(amount.slice(0, -2), 10);
    return { x: p, y: p };
  }
  return { x: 0, y: 0 };
}
