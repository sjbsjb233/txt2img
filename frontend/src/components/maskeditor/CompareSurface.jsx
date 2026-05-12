import { useRef, useState } from "react";
import MEIcon from "./MEIcon.jsx";

export default function CompareSurface({
  beforeUrl,
  afterUrl,
  parentLabel,
  derivationKind,
  derivedLabel,
  heatmapUrl,
  showHeatmap,
}) {
  // The frame is the inner box that holds both images; divider, grip and
  // clipPath all live in the frame's coordinate system so the visual seam
  // stays glued to the grip handle. Pointer math measures the frame, not
  // the outer stage — otherwise the seam (clipped on the image element box)
  // and the grip (positioned in stage %) would only meet at split=0.5.
  const frameRef = useRef(null);
  const [splitX, setSplitX] = useState(0.5); // 0..1
  const draggingRef = useRef(false);

  function onPointerMove(e) {
    if (!draggingRef.current || !frameRef.current) return;
    const r = frameRef.current.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    setSplitX(Math.max(0.02, Math.min(0.98, x)));
  }

  return (
    <div
      className="me-compare-stage"
      onPointerMove={onPointerMove}
      onPointerUp={() => (draggingRef.current = false)}
      data-testid="me-compare-stage"
    >
      {/* Derived breadcrumb */}
      <div className="me-derived-crumb">
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.14em" }}>
          derived
        </span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700 }}>{derivedLabel}</span>
        <span style={{ color: "var(--ink-3)" }}>↰</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-2)" }}>{parentLabel}</span>
        <span className="chip">{derivationKind || "edit"}</span>
      </div>

      <span className="me-compare-label me-compare-label--before">BEFORE · {parentLabel}</span>
      <span className="me-compare-label me-compare-label--after">AFTER · {derivedLabel}</span>

      <div ref={frameRef} className="me-compare-frame">
        {/* Before image — fills the frame; object-fit:contain keeps aspect. */}
        {beforeUrl && (
          <img
            src={beforeUrl}
            alt="before"
            className="me-compare-img"
          />
        )}

        {/* After image, clipped to right of split. Same box as before, so
            the clip seam, divider and grip all share one coordinate frame. */}
        {afterUrl && (
          <img
            src={afterUrl}
            alt="after"
            className="me-compare-img"
            style={{ clipPath: `inset(0 0 0 ${splitX * 100}%)` }}
          />
        )}

        {/* Heatmap overlay — full image, mix-blend multiply */}
        {showHeatmap && heatmapUrl && (
          <img
            src={heatmapUrl}
            alt="diff heatmap"
            className="me-compare-img me-cmp-heatmap"
            data-testid="me-cmp-heatmap"
          />
        )}

        {/* Divider line */}
        <div
          className="me-compare-divider"
          style={{ left: `${splitX * 100}%` }}
        />
        {/* Grip */}
        <div
          className="me-compare-grip"
          style={{
            left: `calc(${splitX * 100}% - 14px)`,
            top: `calc(50% - 14px)`,
          }}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            draggingRef.current = true;
          }}
          data-testid="me-compare-grip"
        >
          ↔
        </div>
        {showHeatmap && heatmapUrl && (
          <div className="me-cmp-heatmap-legend">
            <span className="me-cmp-heatmap-legend__cap">diff intensity</span>
            <div className="me-cmp-heatmap-legend__bar" />
            <span className="me-cmp-heatmap-legend__cap">low → high</span>
          </div>
        )}
      </div>
    </div>
  );
}
