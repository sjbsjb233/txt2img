// Brush + Eraser share the same logic — eraser sets composite to
// destination-out so it removes painted alpha. Both write to the
// mask canvas in image-space coordinates.

const MASK_COLOR = "rgba(226, 75, 74, 0.85)";

export function createBrushTool({ erase = false } = {}) {
  let active = false;
  let lastPt = null;

  return {
    onPointerDown(ctx, pt, opts) {
      active = true;
      lastPt = pt;
      stamp(ctx, pt, opts, erase);
    },
    onPointerMove(ctx, pt, opts) {
      if (!active) return;
      // Linear interpolation between last pt and current pt to avoid
      // gaps when the user moves fast. Step = brush size * spacing %.
      const stepDist = Math.max(
        1,
        opts.size * Math.max(0.05, opts.spacing / 100)
      );
      const dx = pt.x - lastPt.x;
      const dy = pt.y - lastPt.y;
      const dist = Math.hypot(dx, dy);
      const steps = Math.max(1, Math.floor(dist / stepDist));
      for (let i = 1; i <= steps; i++) {
        const ix = lastPt.x + (dx * i) / steps;
        const iy = lastPt.y + (dy * i) / steps;
        stamp(ctx, { x: ix, y: iy }, opts, erase);
      }
      lastPt = pt;
    },
    onPointerUp() {
      const wasActive = active;
      active = false;
      lastPt = null;
      return wasActive;
    },
    isActive() { return active; },
  };
}

function stamp(ctx, pt, opts, erase) {
  const { size, hardness, opacity, _pressure } = opts;
  // _pressure is injected by the canvas layer when `opts.pressure`
  // is true and the pointer event carries a real Force Touch reading.
  // We clamp to 0.1 so the user always lays down at least a faint
  // mark, otherwise the stroke would visibly snap on the first
  // sub-threshold sample.
  const usePressure = !!opts.pressure && typeof _pressure === "number";
  const p = usePressure ? Math.max(0.1, _pressure) : 1;
  const r = (size / 2) * (usePressure ? (0.4 + 0.6 * p) : 1);
  const effOpacity = (opacity / 100) * (usePressure ? (0.5 + 0.5 * p) : 1);
  ctx.save();
  ctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
  if (erase) {
    // For eraser, we want to fully remove alpha — solid white is fine
    // because destination-out only uses alpha.
    ctx.globalAlpha = effOpacity;
    ctx.fillStyle = "rgba(255,255,255,1)";
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.globalAlpha = effOpacity;
    if (hardness >= 99) {
      ctx.fillStyle = MASK_COLOR;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
      ctx.fill();
    } else {
      // Radial gradient emulating soft brush.
      const g = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, r);
      const innerStop = Math.max(0, Math.min(1, hardness / 100));
      g.addColorStop(0, "rgba(226,75,74,0.85)");
      g.addColorStop(innerStop, "rgba(226,75,74,0.85)");
      g.addColorStop(1, "rgba(226,75,74,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.restore();
}
