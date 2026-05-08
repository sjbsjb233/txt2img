// Free-form lasso. Tracks a polyline; on pointer-up, fills the
// closed polygon into the mask. (Polygon and magnetic variants are
// out of scope for this iteration — the design doc lists them as P2.)

const MASK_COLOR = "rgba(226, 75, 74, 0.85)";

export function createLassoTool() {
  let pts = [];
  return {
    onPointerDown(_ctx, pt) {
      pts = [pt];
    },
    onPointerMove(_ctx, pt) {
      if (!pts.length) return;
      pts.push(pt);
    },
    onPointerUp(ctx) {
      if (pts.length < 3) {
        pts = [];
        return false;
      }
      ctx.save();
      ctx.fillStyle = MASK_COLOR;
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
      pts = [];
      return true;
    },
    getPreview() {
      if (!pts.length) return null;
      return pts;
    },
    isActive() { return pts.length > 0; },
  };
}
