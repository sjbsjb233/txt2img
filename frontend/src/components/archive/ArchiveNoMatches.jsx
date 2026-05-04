// Compact "filtered to zero" empty state. Mirrors the Empty States
// design's "Archive · filtered, no matches" frame: hatch surface,
// magnifying-glass-with-zero glyph, italic display caption with a
// banana-highlighted slice, mono sub-copy, and a single "Clear all
// filters" action. The other actions in the design (Widen period to
// 7d, Save this query, NEAREST MATCH teaser) are intentionally out of
// scope for v1.

export default function ArchiveNoMatches({ onClear }) {
  return (
    <div
      data-testid="archive-no-matches"
      className="arch-no-matches-in"
      style={{
        marginTop: 28,
        position: "relative",
        border: "1px solid var(--ink)",
        background: "#fffdf7",
        minHeight: 360,
        overflow: "hidden",
      }}
    >
      {/* Dense hatch background — the same texture used for empty surfaces
          in the design system. Sits behind the centered caption. */}
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          background:
            "repeating-linear-gradient(135deg, var(--paper-2) 0 6px, var(--paper-3) 6px 12px)",
        }}
      />

      <div
        style={{
          position: "relative",
          zIndex: 2,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 360,
          padding: 40,
          gap: 22,
        }}
      >
        {/* Magnifying-glass-with-zero glyph, drawn in CSS. */}
        <div style={{ position: "relative", width: 84, height: 84 }}>
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: 64,
              height: 64,
              border: "2px solid var(--ink)",
              borderRadius: "50%",
              background: "#fffdf7",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: "var(--font-display)",
              fontWeight: 900,
              fontSize: 32,
              fontStyle: "italic",
              color: "var(--ink-3)",
            }}
          >
            0
          </div>
          <div
            style={{
              position: "absolute",
              bottom: 0,
              right: 0,
              width: 32,
              height: 2,
              background: "var(--ink)",
              transform: "rotate(45deg)",
              transformOrigin: "left center",
            }}
          />
        </div>

        <div
          style={{
            textAlign: "center",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
          }}
        >
          <div
            className="display"
            style={{
              fontSize: 22,
              fontStyle: "italic",
              fontWeight: 700,
              letterSpacing: "-0.01em",
              lineHeight: 1.15,
              color: "var(--ink)",
              maxWidth: 460,
            }}
          >
            No matches for{" "}
            <span style={{ background: "var(--banana)", padding: "0 4px" }}>
              this slice
            </span>{" "}
            of your archive.
          </div>
          <div
            className="mono"
            style={{
              fontSize: 11,
              color: "var(--ink-3)",
              lineHeight: 1.6,
              maxWidth: 420,
            }}
          >
            You've still made plenty — just not under these filters. Try
            widening one, or clear them all and start over.
          </div>
        </div>

        <button
          type="button"
          className="arch-press"
          onClick={onClear}
          style={{
            padding: "9px 18px",
            background: "var(--ink)",
            color: "var(--paper)",
            border: "1px solid var(--ink)",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            fontWeight: 700,
            boxShadow: "3px 3px 0 var(--ink)",
          }}
        >
          Clear all filters
        </button>
      </div>
    </div>
  );
}
