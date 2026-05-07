// ComparePane — one half of the picker compare surface.
//
// Visuals match Variation B: caps tone-coloured label on the left,
// mono sub-text on the right, then an aspect-correct image pane
// optionally outlined with the banana current-state border.

import AuthorizedImage from "../AuthorizedImage.jsx";

export default function ComparePane({ label, image, sub, tone = "muted", big = false }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        minWidth: 0,
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span
          className="caps"
          style={{
            fontSize: 10,
            background: tone === "banana" ? "var(--banana)" : "var(--ink)",
            color: tone === "banana" ? "var(--ink)" : "var(--paper)",
            padding: "3px 8px",
          }}
        >
          {label}
        </span>
        {sub && (
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            {sub}
          </span>
        )}
      </div>
      <div
        className={`pk-img-pane ${big && tone === "banana" ? "current" : ""}`}
        style={{
          flex: 1,
          minHeight: 0,
          aspectRatio: image ? `${image.width} / ${image.height}` : "1 / 1",
          opacity: image ? 1 : 0.5,
          alignSelf: "center",
          maxWidth: "100%",
        }}
      >
        {image ? (
          <>
            <AuthorizedImage
              src={image.thumb_url}
              alt=""
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
              }}
            />
            <div
              style={{
                position: "absolute",
                top: 8,
                right: 8,
                display: "flex",
                gap: 4,
              }}
            >
              <span
                className="pk-chip"
                style={{ background: "var(--paper-soft)", fontSize: 10 }}
              >
                {image.width}×{image.height}
              </span>
            </div>
          </>
        ) : (
          <div
            className="pk-hatch"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--ink-3)",
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 18,
            }}
          >
            no image
          </div>
        )}
      </div>
    </div>
  );
}
