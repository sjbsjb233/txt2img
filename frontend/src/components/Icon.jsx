export default function Icon({ name, size = 16, stroke = "currentColor" }) {
  const s = {
    width: size,
    height: size,
    stroke,
    strokeWidth: 1.5,
    fill: "none",
    strokeLinecap: "round",
    strokeLinejoin: "round",
  };
  const paths = {
    plus: <path d="M8 2v12M2 8h12" />,
    spark: (
      <path d="M8 1v4M8 11v4M1 8h4M11 8h4M3.5 3.5l2.5 2.5M10 10l2.5 2.5M3.5 12.5L6 10M10 6l2.5-2.5" />
    ),
    arrow: <path d="M3 8h10M9 4l4 4-4 4" />,
    image: (
      <>
        <rect x="2" y="2" width="12" height="12" />
        <circle cx="6" cy="6" r="1.2" />
        <path d="M2 11l3-3 4 4 2-2 3 3" />
      </>
    ),
    grid: (
      <>
        <rect x="2" y="2" width="5" height="5" />
        <rect x="9" y="2" width="5" height="5" />
        <rect x="2" y="9" width="5" height="5" />
        <rect x="9" y="9" width="5" height="5" />
      </>
    ),
    stack: <path d="M2 5l6-3 6 3-6 3-6-3zM2 8l6 3 6-3M2 11l6 3 6-3" />,
    clock: (
      <>
        <circle cx="8" cy="8" r="6" />
        <path d="M8 4v4l2.5 2" />
      </>
    ),
    star: <path d="M8 2l1.8 3.8 4.2.6-3 3 .8 4.1L8 11.6 4.2 13.5 5 9.4 2 6.4l4.2-.6z" />,
    bolt: <path d="M9 2L3 9h4l-1 5 6-7H8l1-5z" fill="currentColor" stroke="none" />,
    check: <path d="M3 8l3.5 3.5L13 5" />,
    dot: <circle cx="8" cy="8" r="2" fill="currentColor" stroke="none" />,
    close: <path d="M3 3l10 10M13 3L3 13" />,
    search: (
      <>
        <circle cx="7" cy="7" r="4.5" />
        <path d="M10.5 10.5L14 14" />
      </>
    ),
    filter: <path d="M2 3h12l-4.5 6v4l-3 1.5V9z" />,
    user: (
      <>
        <circle cx="8" cy="5.5" r="2.5" />
        <path d="M3 14c1-3 3-4 5-4s4 1 5 4" />
      </>
    ),
    gear: (
      <>
        <circle cx="8" cy="8" r="2.5" />
        <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3" />
      </>
    ),
    chart: <path d="M2 13h12M4 13V8M7 13V4M10 13V9M13 13V6" />,
    upload: <path d="M8 10V2M4 6l4-4 4 4M2 13h12" />,
    download: <path d="M8 2v9M4 7l4 4 4-4M2 14h12" />,
    archive: (
      <>
        <rect x="2" y="3" width="12" height="3" />
        <path d="M3 6v8h10V6M6 9h4" />
      </>
    ),
    "double-chevron-left": <path d="M7 4L3 8l4 4M13 4L9 8l4 4" />,
    "double-chevron-right": <path d="M3 4l4 4-4 4M9 4l4 4-4 4" />,
    logout: (
      <>
        <path d="M9 2H3v12h6" />
        <path d="M7 8h8M11 4l4 4-4 4" />
      </>
    ),
  };
  return (
    <svg viewBox="0 0 16 16" style={s}>
      {paths[name] || paths.dot}
    </svg>
  );
}
