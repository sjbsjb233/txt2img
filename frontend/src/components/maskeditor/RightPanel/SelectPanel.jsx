import { Field, Slider } from "./atoms.jsx";

export default function SelectPanel({ tool, brushOpts, setBrushOpts }) {
  function patch(name, value) {
    setBrushOpts({ ...brushOpts, [name]: value });
  }
  return (
    <div className="me-panel-body">
      {tool === "wand" && (
        <Field label="tolerance" extra={`${Math.round(brushOpts.tolerance ?? 32)}`}>
          <Slider
            value={brushOpts.tolerance ?? 32}
            min={1}
            max={100}
            onChange={(v) => patch("tolerance", v)}
          />
        </Field>
      )}
      {tool === "rect" && (
        <p style={{ fontSize: 12, color: "var(--ink-3)" }}>
          drag on the image to add a rectangular mask region.
        </p>
      )}
      {tool === "lasso" && (
        <p style={{ fontSize: 12, color: "var(--ink-3)" }}>
          drag a freeform shape; release to close and add to mask.
        </p>
      )}
      {tool === "pan" && (
        <p style={{ fontSize: 12, color: "var(--ink-3)" }}>
          drag to pan the canvas. zoom with mouse wheel.
        </p>
      )}
    </div>
  );
}
