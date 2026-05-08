import { Field, Slider, Toggle } from "./atoms.jsx";

export default function BrushPanel({ brushOpts, setBrushOpts }) {
  function patch(name, value) {
    setBrushOpts({ ...brushOpts, [name]: value });
  }
  return (
    <div className="me-panel-body">
      <Field label="size" extra={`${Math.round(brushOpts.size)} px`}>
        <Slider
          value={brushOpts.size}
          min={1}
          max={200}
          onChange={(v) => patch("size", v)}
        />
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-4)" }}>1</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-4)" }}>[ / ] to adjust</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-4)" }}>200</span>
        </div>
      </Field>
      <Field label="hardness" extra={`${Math.round(brushOpts.hardness)} %`}>
        <Slider value={brushOpts.hardness} onChange={(v) => patch("hardness", v)} />
      </Field>
      <Field label="opacity" extra={`${Math.round(brushOpts.opacity)} %`}>
        <Slider value={brushOpts.opacity} onChange={(v) => patch("opacity", v)} />
      </Field>
      <Field label="spacing" extra={`${Math.round(brushOpts.spacing)} %`}>
        <Slider value={brushOpts.spacing} max={50} onChange={(v) => patch("spacing", v)} />
      </Field>
      <div style={{ borderTop: "1px solid var(--rule)", margin: "16px -16px 12px", padding: "12px 16px 0" }}>
        <Toggle on={brushOpts.smoothing} label="smoothing (Catmull-Rom)" onChange={(v) => patch("smoothing", v)} />
        <Toggle on={brushOpts.pressure} label="pen pressure → size + opacity" onChange={(v) => patch("pressure", v)} />
      </div>
      <Field label="brush preview" extra={`${Math.round(brushOpts.hardness)}% hardness`}>
        <div className="me-brush-preview">
          <div
            className="me-brush-preview__circle"
            style={{ opacity: brushOpts.opacity / 100 }}
          />
        </div>
      </Field>
    </div>
  );
}
