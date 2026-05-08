import { useEffect, useRef, useState } from "react";
import MEIcon from "./MEIcon.jsx";

export default function CompareSurface({ beforeUrl, afterUrl, parentLabel, derivationKind, derivedLabel }) {
  const containerRef = useRef(null);
  const [splitX, setSplitX] = useState(0.5); // 0..1
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 });
  const draggingRef = useRef(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const update = () => {
      const r = el.getBoundingClientRect();
      setContainerSize({ w: r.width, h: r.height });
    };
    update();
    const obs = new ResizeObserver(update);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  function onPointerMove(e) {
    if (!draggingRef.current) return;
    const r = containerRef.current.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    setSplitX(Math.max(0.02, Math.min(0.98, x)));
  }

  return (
    <div
      ref={containerRef}
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

      {/* Before image */}
      {beforeUrl && (
        <img
          src={beforeUrl}
          alt="before"
          style={{
            position: "absolute",
            inset: 0,
            margin: "auto",
            maxWidth: "90%",
            maxHeight: "85%",
            objectFit: "contain",
            border: "1px solid var(--ink)",
            background: "#fff",
          }}
        />
      )}

      {/* After image, clipped to right of split */}
      {afterUrl && (
        <img
          src={afterUrl}
          alt="after"
          style={{
            position: "absolute",
            inset: 0,
            margin: "auto",
            maxWidth: "90%",
            maxHeight: "85%",
            objectFit: "contain",
            clipPath: `inset(0 0 0 ${splitX * 100}%)`,
            border: "1px solid var(--ink)",
            background: "#fff",
          }}
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
    </div>
  );
}
