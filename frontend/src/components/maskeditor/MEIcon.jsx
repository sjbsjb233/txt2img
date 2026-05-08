// Mask-editor specific icon set (20x20 viewBox, lifted from the
// design prototype's MEIcon component).
//
// This is intentionally separate from the project-wide ``Icon`` (16x16
// viewBox) so the editor's tools can have higher-detail glyphs without
// changing how the rest of the app draws.

export default function MEIcon({
  name,
  size = 16,
  stroke = "currentColor",
  strokeWidth = 1.5,
}) {
  const s = {
    width: size,
    height: size,
    stroke,
    strokeWidth,
    fill: "none",
    strokeLinecap: "round",
    strokeLinejoin: "round",
  };
  const paths = {
    back: <path d="M10 4L4 10l6 6M4 10h12" />,
    brush: (
      <>
        <path d="M14 2c-1 1-6 6-7 7l3 3c1-1 6-6 7-7l-3-3z" />
        <path d="M7 9l-3 3c-1 1-1 3 0 4s3 1 4 0l3-3" />
      </>
    ),
    eraser: (
      <>
        <path d="M3 13l7-7 5 5-7 7H4l-1-1v-4z" />
        <path d="M8 8l5 5" />
      </>
    ),
    "rect-marquee": (
      <rect x="3" y="3" width="14" height="14" strokeDasharray="3 2" />
    ),
    lasso: (
      <path d="M4 5c2-3 8-3 11-1s3 6 0 8-9 2-11 0c-1-1 0-2 1-2 2 0 3 2 2 4l-1 4" />
    ),
    wand: (
      <path d="M3 17l8-8M9 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM15 5l1 1M16 10l1 1M11 3v0" />
    ),
    pan: (
      <path d="M10 2v8M10 10c0-2-2-3-3-1l-1 3 2 5h6l1-5v-3c0-1-2-1-2 0v-3c0-1-2-1-2 0z" />
    ),
    invert: (
      <>
        <circle cx="10" cy="10" r="7" />
        <path d="M10 3a7 7 0 0 0 0 14V3z" fill="currentColor" />
      </>
    ),
    feather: (
      <>
        <path d="M3 17L10 10M10 10c4 0 7-3 7-7-4 0-7 3-7 7zM5 14l3-3" />
      </>
    ),
    expand: (
      <>
        <rect x="5" y="5" width="10" height="10" />
        <path d="M2 2l3 3M18 2l-3 3M2 18l3-3M18 18l-3-3" />
      </>
    ),
    contract: (
      <>
        <rect x="3" y="3" width="14" height="14" />
        <path d="M7 7l3 3M13 7l-3 3M7 13l3-3M13 13l-3-3" />
      </>
    ),
    "select-all": (
      <>
        <rect x="3" y="3" width="14" height="14" strokeDasharray="2 2" />
        <rect x="6" y="6" width="8" height="8" fill="currentColor" />
      </>
    ),
    deselect: (
      <>
        <rect x="3" y="3" width="14" height="14" strokeDasharray="2 2" />
        <path d="M5 5l10 10M15 5L5 15" />
      </>
    ),
    smooth: <path d="M3 14C5 14 5 6 8 6s3 8 6 8 3-4 3-4" />,
    "zoom-in": (
      <>
        <circle cx="9" cy="9" r="6" />
        <path d="M14 14l4 4M6 9h6M9 6v6" />
      </>
    ),
    "zoom-out": (
      <>
        <circle cx="9" cy="9" r="6" />
        <path d="M14 14l4 4M6 9h6" />
      </>
    ),
    fit: <path d="M3 7V3h4M17 7V3h-4M3 13v4h4M17 13v4h-4" />,
    undo: (
      <>
        <path d="M5 9l-3-3 3-3M2 6h10c3 0 5 2 5 5s-2 5-5 5H7" />
      </>
    ),
    redo: (
      <>
        <path d="M15 9l3-3-3-3M18 6H8c-3 0-5 2-5 5s2 5 5 5h5" />
      </>
    ),
    outpaint: (
      <>
        <rect x="6" y="6" width="8" height="8" />
        <path d="M2 2v3M2 2h3M18 2v3M18 2h-3M2 18v-3M2 18h3M18 18v-3M18 18h-3" />
      </>
    ),
    submit: <path d="M3 10l5 5 9-12" />,
    warn: (
      <>
        <path d="M10 2L2 17h16L10 2z" />
        <path d="M10 8v4M10 15v0" />
      </>
    ),
    ok: (
      <>
        <circle cx="10" cy="10" r="7" />
        <path d="M6 10l3 3 5-6" />
      </>
    ),
    trash: <path d="M3 5h14M7 5V3h6v2M5 5l1 12h8l1-12" />,
    history: (
      <>
        <circle cx="10" cy="10" r="7" />
        <path d="M10 5v5l3 2M3 6l-1 3 3 1" />
      </>
    ),
    plus: <path d="M10 3v14M3 10h14" />,
    close: <path d="M4 4l12 12M16 4L4 16" />,
    help: (
      <>
        <circle cx="10" cy="10" r="7" />
        <path d="M7.5 8c0-2 5-2 5 0 0 1.5-2.5 1.5-2.5 4M10 14v0" />
      </>
    ),
  };
  return (
    <svg viewBox="0 0 20 20" style={s}>
      {paths[name] || (
        <circle cx="10" cy="10" r="3" fill="currentColor" stroke="none" />
      )}
    </svg>
  );
}
