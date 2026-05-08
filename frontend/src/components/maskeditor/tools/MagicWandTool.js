// Magic wand: BFS flood-fill from the click point on the source
// image, selecting all pixels within a color tolerance, then writes
// the resulting region into the mask layer.

const MASK_COLOR = [226, 75, 74, 217]; // 0.85 * 255

export function createMagicWandTool() {
  return {
    onPointerDown(maskCtx, pt, opts) {
      const { sourceCanvas, tolerance = 32 } = opts;
      if (!sourceCanvas) return;
      floodFill(maskCtx, sourceCanvas, pt, tolerance);
      return true;
    },
    onPointerMove() {},
    onPointerUp() { return false; },
    isActive() { return false; },
  };
}

function floodFill(maskCtx, sourceCanvas, pt, tolerance) {
  const w = sourceCanvas.width;
  const h = sourceCanvas.height;
  const sx = Math.round(pt.x);
  const sy = Math.round(pt.y);
  if (sx < 0 || sx >= w || sy < 0 || sy >= h) return;

  const sCtx = sourceCanvas.getContext("2d");
  const src = sCtx.getImageData(0, 0, w, h).data;
  const target = maskCtx.getImageData(0, 0, w, h);
  const data = target.data;

  const startIdx = (sy * w + sx) * 4;
  const r0 = src[startIdx];
  const g0 = src[startIdx + 1];
  const b0 = src[startIdx + 2];
  const tol2 = tolerance * tolerance * 3;

  const visited = new Uint8Array(w * h);
  const queue = [sy * w + sx];
  visited[sy * w + sx] = 1;

  // Cap pixels processed to keep latency tame.
  const MAX_PIXELS = w * h;
  let processed = 0;
  while (queue.length && processed < MAX_PIXELS) {
    const idx = queue.shift();
    processed++;
    const py = (idx / w) | 0;
    const px = idx % w;
    const i4 = idx * 4;
    const r = src[i4];
    const g = src[i4 + 1];
    const b = src[i4 + 2];
    const d2 = (r - r0) ** 2 + (g - g0) ** 2 + (b - b0) ** 2;
    if (d2 > tol2) continue;
    data[i4] = MASK_COLOR[0];
    data[i4 + 1] = MASK_COLOR[1];
    data[i4 + 2] = MASK_COLOR[2];
    data[i4 + 3] = MASK_COLOR[3];
    // Neighbours
    if (px + 1 < w && !visited[idx + 1]) { visited[idx + 1] = 1; queue.push(idx + 1); }
    if (px > 0 && !visited[idx - 1]) { visited[idx - 1] = 1; queue.push(idx - 1); }
    if (py + 1 < h && !visited[idx + w]) { visited[idx + w] = 1; queue.push(idx + w); }
    if (py > 0 && !visited[idx - w]) { visited[idx - w] = 1; queue.push(idx - w); }
  }
  maskCtx.putImageData(target, 0, 0);
}
