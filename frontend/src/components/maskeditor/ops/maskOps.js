// Pure-canvas mask post-processing ops.
//
// All ops mutate the alpha channel of the supplied mask canvas. RGB
// is left at its display tint (red). The functions return nothing —
// the caller is expected to push to history before/after.

export function applyAll(maskCanvas, color = "rgba(226,75,74,0.85)") {
  const ctx = maskCanvas.getContext("2d");
  ctx.save();
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, maskCanvas.width, maskCanvas.height);
  ctx.restore();
}

export function applyClear(maskCanvas) {
  const ctx = maskCanvas.getContext("2d");
  ctx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
}

export function applyInvert(maskCanvas, color = [226, 75, 74]) {
  const ctx = maskCanvas.getContext("2d");
  const w = maskCanvas.width;
  const h = maskCanvas.height;
  const img = ctx.getImageData(0, 0, w, h);
  for (let i = 0; i < img.data.length; i += 4) {
    const inv = 255 - img.data[i + 3];
    img.data[i] = color[0];
    img.data[i + 1] = color[1];
    img.data[i + 2] = color[2];
    img.data[i + 3] = inv;
  }
  ctx.putImageData(img, 0, 0);
}

// Gaussian blur the alpha channel — emulates "feather". Box blur
// approximation, two passes for smoother results. Radius is in image
// pixels, capped to 50 to keep latency tame.
export function applyFeather(maskCanvas, radius = 6) {
  const r = Math.max(1, Math.min(50, Math.round(radius)));
  const ctx = maskCanvas.getContext("2d");
  const w = maskCanvas.width;
  const h = maskCanvas.height;
  const img = ctx.getImageData(0, 0, w, h);
  // Two-pass box blur on alpha channel only.
  const alpha = new Uint8ClampedArray(w * h);
  for (let i = 0, j = 3; j < img.data.length; i++, j += 4) {
    alpha[i] = img.data[j];
  }
  const tmp = new Uint8ClampedArray(w * h);
  boxBlurH(alpha, tmp, w, h, r);
  boxBlurV(tmp, alpha, w, h, r);
  for (let i = 0, j = 3; j < img.data.length; i++, j += 4) {
    img.data[j] = alpha[i];
  }
  ctx.putImageData(img, 0, 0);
}

function boxBlurH(src, dst, w, h, r) {
  for (let y = 0; y < h; y++) {
    let acc = 0;
    let count = 0;
    // Prime
    for (let x = 0; x < r && x < w; x++) {
      acc += src[y * w + x];
      count++;
    }
    for (let x = 0; x < w; x++) {
      const xPlus = x + r;
      const xMinus = x - r - 1;
      if (xPlus < w) { acc += src[y * w + xPlus]; count++; }
      if (xMinus >= 0) { acc -= src[y * w + xMinus]; count--; }
      dst[y * w + x] = acc / Math.max(1, count);
    }
  }
}
function boxBlurV(src, dst, w, h, r) {
  for (let x = 0; x < w; x++) {
    let acc = 0;
    let count = 0;
    for (let y = 0; y < r && y < h; y++) {
      acc += src[y * w + x];
      count++;
    }
    for (let y = 0; y < h; y++) {
      const yPlus = y + r;
      const yMinus = y - r - 1;
      if (yPlus < h) { acc += src[yPlus * w + x]; count++; }
      if (yMinus >= 0) { acc -= src[yMinus * w + x]; count--; }
      dst[y * w + x] = acc / Math.max(1, count);
    }
  }
}

// Morphological dilate (expand) on alpha channel. Square kernel.
export function applyExpand(maskCanvas, radius = 5, color = [226, 75, 74]) {
  morphology(maskCanvas, radius, "max", color);
}

// Morphological erode (contract).
export function applyContract(maskCanvas, radius = 5, color = [226, 75, 74]) {
  morphology(maskCanvas, radius, "min", color);
}

function morphology(maskCanvas, radius, op, color) {
  const r = Math.max(1, Math.min(30, Math.round(radius)));
  const ctx = maskCanvas.getContext("2d");
  const w = maskCanvas.width;
  const h = maskCanvas.height;
  const img = ctx.getImageData(0, 0, w, h);
  const alpha = new Uint8ClampedArray(w * h);
  for (let i = 0, j = 3; j < img.data.length; i++, j += 4) alpha[i] = img.data[j];
  const tmp = new Uint8ClampedArray(w * h);
  // Horizontal pass
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = op === "max" ? 0 : 255;
      for (let k = -r; k <= r; k++) {
        const nx = x + k;
        if (nx < 0 || nx >= w) continue;
        const a = alpha[y * w + nx];
        v = op === "max" ? Math.max(v, a) : Math.min(v, a);
      }
      tmp[y * w + x] = v;
    }
  }
  // Vertical pass
  for (let x = 0; x < w; x++) {
    for (let y = 0; y < h; y++) {
      let v = op === "max" ? 0 : 255;
      for (let k = -r; k <= r; k++) {
        const ny = y + k;
        if (ny < 0 || ny >= h) continue;
        const a = tmp[ny * w + x];
        v = op === "max" ? Math.max(v, a) : Math.min(v, a);
      }
      alpha[y * w + x] = v;
    }
  }
  for (let i = 0, j = 0; j < img.data.length; i++, j += 4) {
    img.data[j] = color[0];
    img.data[j + 1] = color[1];
    img.data[j + 2] = color[2];
    img.data[j + 3] = alpha[i];
  }
  ctx.putImageData(img, 0, 0);
}

// Cheap "smooth" — just do a small feather. Good enough.
export function applySmooth(maskCanvas, strength = 3) {
  applyFeather(maskCanvas, Math.max(1, strength));
}
