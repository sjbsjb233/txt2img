// Export the in-canvas mask as a PNG Blob ready for upload.
//
// Internal contract: the mask layer's RGBA canvas is the user's
// painting. RGB is purely for display tint (we use red); the alpha
// channel is the only real signal — alpha > 0 means "the user wants
// this region changed".
//
// OpenAI's /v1/images/edits expects the OPPOSITE convention: alpha=0
// = repaint, alpha=255 = preserve. We invert at export time, so the
// in-editor data structure remains ergonomic ("painted = will change").

export async function exportMaskPng(maskCanvas) {
  const w = maskCanvas.width;
  const h = maskCanvas.height;
  const off = document.createElement("canvas");
  off.width = w;
  off.height = h;
  const ctx = off.getContext("2d");
  // Start fully opaque (preserve everywhere).
  ctx.fillStyle = "rgba(255,255,255,1)";
  ctx.fillRect(0, 0, w, h);

  const sourceCtx = maskCanvas.getContext("2d");
  const src = sourceCtx.getImageData(0, 0, w, h);
  const out = ctx.getImageData(0, 0, w, h);
  for (let i = 0; i < src.data.length; i += 4) {
    // Inverted alpha: painted area (high alpha in source) -> alpha=0 in out.
    out.data[i + 3] = 255 - src.data[i + 3];
  }
  ctx.putImageData(out, 0, 0);

  return await new Promise((resolve, reject) => {
    off.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("toBlob failed"))),
      "image/png"
    );
  });
}

// Count the number of "painted" pixels (alpha >= 128) on the mask.
// Used by the empty-mask validation step.
export function countMaskPaintedPixels(maskCanvas) {
  const ctx = maskCanvas.getContext("2d");
  const data = ctx.getImageData(0, 0, maskCanvas.width, maskCanvas.height).data;
  let count = 0;
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] >= 128) count++;
  }
  return count;
}

// Return the painted ratio (0..1).
export function maskPaintedRatio(maskCanvas) {
  const total = maskCanvas.width * maskCanvas.height;
  if (!total) return 0;
  return countMaskPaintedPixels(maskCanvas) / total;
}
