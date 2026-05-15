// Canvas-area skeleton shown while the source image is still being
// fetched/decoded, OR while a soft history switch is replacing the
// current image. Page chrome (top bar / toolbar / right panel / status
// bar) stays mounted around it so the editor never "blanks out".

export default function CanvasSkeleton({ message = "loading image…", error = null, onBack }) {
  if (error) {
    return (
      <div className="me-canvas-skeleton" data-testid="me-canvas-skeleton" data-state="error">
        <div className="me-canvas-skeleton__card me-canvas-skeleton__card--error">
          <div className="me-canvas-skeleton__title">couldn't load image</div>
          <div className="me-canvas-skeleton__message">{error}</div>
          {onBack && (
            <button className="btn sm" onClick={onBack} data-testid="me-skeleton-back">
              back to archive
            </button>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="me-canvas-skeleton" data-testid="me-canvas-skeleton" data-state="loading">
      <div className="me-canvas-skeleton__rect">
        <div className="me-canvas-skeleton__shimmer" />
      </div>
      <div className="me-canvas-skeleton__label">{message}</div>
    </div>
  );
}
