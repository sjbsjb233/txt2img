// In-canvas overlay shown during a generation when the active model
// supports partial-image streaming. Sits between CanvasStage and the
// LockVeil — its blurred preview, scanline, and marching-ants border
// give the user something to watch while the real result lands.

export default function CanvasPartial({
  partialUrl = null,
  partialIndex = 1,
  partialMax = 2,
  elapsedSec = 0,
  size = 320,
}) {
  return (
    <div
      className="me-canvas-partial"
      data-testid="me-canvas-partial"
      style={{
        left: "50%",
        top: "50%",
        width: size,
        height: size,
        transform: "translate(-50%, -50%)",
      }}
    >
      {partialUrl && (
        <img className="me-canvas-partial__img" src={partialUrl} alt="" />
      )}
      <div className="me-canvas-partial__scan" />
      <div
        className="me-canvas-partial__tag"
        data-testid="me-canvas-partial-badge"
      >
        partial {partialIndex} / {partialMax} · {Math.round(elapsedSec)}s
      </div>
      <svg className="me-canvas-partial__svg">
        <rect
          x="1"
          y="1"
          width="calc(100% - 2px)"
          height="calc(100% - 2px)"
          className="me-canvas-partial__rect"
        />
      </svg>
    </div>
  );
}
