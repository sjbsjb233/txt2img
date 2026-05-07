// Filmstrip — colour-coded per-image bar. Shows in-flight placeholders
// at the end. Mirrors the Variation B prototype's FilmstripHover atom.

import { useRef, useState } from "react";
import AuthorizedImage from "../AuthorizedImage.jsx";

export default function Filmstrip({
  images,
  cursorIdx,
  onPick,
  inflightR = 0,
  inflightQ = 0,
  inflightF = 0,
  height = 22,
  padded = true,
  dark = false,
}) {
  const [hover, setHover] = useState(null);
  const containerRef = useRef(null);
  const [mouseX, setMouseX] = useState(0);

  return (
    <div
      ref={containerRef}
      onMouseMove={(e) => {
        const rect = containerRef.current?.getBoundingClientRect();
        if (rect) setMouseX(e.clientX - rect.left);
      }}
      style={{
        position: "relative",
        padding: padded ? "8px 22px 6px" : 0,
      }}
    >
      {hover !== null && images[hover] && (
        <div
          style={{
            position: "absolute",
            bottom: padded ? "calc(100% - 4px)" : "calc(100% + 6px)",
            left: Math.max(
              60,
              Math.min((containerRef.current?.offsetWidth || 0) - 60, mouseX)
            ),
            transform: "translateX(-50%)",
            width: 120,
            height: 120,
            border: dark ? "1.5px solid #ffffff80" : "1.5px solid var(--ink)",
            boxShadow: dark
              ? "0 4px 0 #00000080"
              : "0 4px 0 var(--ink)",
            background: "var(--paper-3)",
            zIndex: 30,
            pointerEvents: "none",
            overflow: "hidden",
          }}
        >
          <AuthorizedImage
            src={images[hover].thumb_url}
            alt=""
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              display: "block",
            }}
          />
          <span
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              background: "var(--ink)",
              color: "var(--paper)",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              padding: "1px 5px",
            }}
          >
            #{hover + 1} · {images[hover].pick_state}
          </span>
        </div>
      )}

      <div className="pk-fs-row" style={{ height }}>
        {images.map((im, i) => (
          <div
            key={im.image_id}
            className={`pk-fs-cell ${im.pick_state || "unjudged"} ${
              i === cursorIdx ? "current" : ""
            }`}
            onClick={() => onPick && onPick(i)}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            style={{ height }}
          />
        ))}
        {Array.from({ length: inflightR }).map((_, i) => (
          <div key={`r${i}`} className="pk-fs-cell running" style={{ height }} />
        ))}
        {Array.from({ length: inflightQ }).map((_, i) => (
          <div key={`q${i}`} className="pk-fs-cell queued" style={{ height }} />
        ))}
        {Array.from({ length: inflightF }).map((_, i) => (
          <div key={`f${i}`} className="pk-fs-cell failed" style={{ height }} />
        ))}
      </div>
    </div>
  );
}
