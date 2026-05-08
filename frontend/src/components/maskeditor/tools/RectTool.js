// Rectangle marquee tool. On pointer-up, paints the rectangle into
// the mask layer at full opacity (red). Modifier keys would add/sub,
// but we keep things simple — replace mode only for this iteration.

const MASK_COLOR = "rgba(226, 75, 74, 0.85)";

export function createRectTool() {
  let start = null;
  let current = null;
  return {
    onPointerDown(_ctx, pt) {
      start = pt;
      current = pt;
    },
    onPointerMove(_ctx, pt) {
      if (!start) return;
      current = pt;
    },
    onPointerUp(ctx) {
      if (!start || !current) return false;
      const x = Math.min(start.x, current.x);
      const y = Math.min(start.y, current.y);
      const w = Math.abs(current.x - start.x);
      const h = Math.abs(current.y - start.y);
      const wasActive = w > 1 && h > 1;
      if (wasActive) {
        ctx.save();
        ctx.fillStyle = MASK_COLOR;
        ctx.fillRect(x, y, w, h);
        ctx.restore();
      }
      start = null;
      current = null;
      return wasActive;
    },
    getPreview() {
      if (!start || !current) return null;
      return {
        x: Math.min(start.x, current.x),
        y: Math.min(start.y, current.y),
        w: Math.abs(current.x - start.x),
        h: Math.abs(current.y - start.y),
      };
    },
    isActive() { return start !== null; },
  };
}
