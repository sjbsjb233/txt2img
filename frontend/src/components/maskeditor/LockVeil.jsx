// Full-page diagonal-stripe lock that overlays the editor while a job
// is in flight. Far more honest than a global opacity dim — the
// striped pattern + LOCKED labels make it impossible to mistake for
// "things just look sleepy."
//
// The veil is split into 5 panels matching the editor's chrome
// geometry. The canvas panel uses a softer overlay so the
// CanvasPartial preview underneath can shine through; the others
// use the full-strength stripe.
//
// Sits at z-index 10 (above CanvasStage / CanvasPartial, below
// GeneratingCard at 12 and the back-only overlay at 11).

const TOP_H = 60;
const RAIL_W = 57;
const RIGHT_W = 320;
const STATUS_H = 32;

export default function LockVeil() {
  return (
    <div className="me-lock-veil" data-testid="me-lock-veil" aria-hidden="true">
      {/* top bar */}
      <div
        className="me-lock-veil__stripe"
        style={{ top: 0, left: 0, right: 0, height: TOP_H }}
      />
      {/* tool rail */}
      <div
        className="me-lock-veil__stripe"
        style={{ top: TOP_H, left: 0, width: RAIL_W, bottom: STATUS_H }}
      />
      {/* canvas area — softer overlay */}
      <div
        className="me-lock-veil__stripe me-lock-veil__stripe--canvas"
        style={{
          top: TOP_H,
          left: RAIL_W,
          right: RIGHT_W,
          bottom: STATUS_H,
        }}
      />
      {/* right panel */}
      <div
        className="me-lock-veil__stripe"
        style={{ top: TOP_H, right: 0, width: RIGHT_W, bottom: STATUS_H }}
      />
      {/* status bar */}
      <div
        className="me-lock-veil__stripe"
        style={{ bottom: 0, left: 0, right: 0, height: STATUS_H }}
      />
      {/* Vertical LOCKED label sits inside the toolbar gutter so the
          eye instantly registers "this whole left rail is frozen." */}
      <div
        className="me-lock-badge--rail"
        data-testid="me-lock-rail-badge"
        style={{ top: TOP_H + 16, left: 8, width: RAIL_W - 16, height: 96 }}
      >
        LOCKED
      </div>
      {/* Right-panel pill — visual signature for the ongoing work. */}
      <div
        className="me-lock-badge--right"
        data-testid="me-lock-right-badge"
        style={{ top: TOP_H + 16, right: 16 }}
      >
        LOCKED · GENERATING
      </div>
    </div>
  );
}
