// Export the in-canvas mask as a PNG Blob ready for upload.
//
// Internal contract: the mask layer's RGBA canvas is the user's
// painting. RGB is purely for display tint (we use red); the alpha
// channel is the only real signal — alpha > 0 means "the user wants
// this region changed".
//
// OpenAI's /v1/images/edits expects the OPPOSITE convention. Per
// the API spec:
//
//   "Transparent areas (e.g. where alpha is 0) indicate where the
//    image should be edited."
//
// Importantly, gpt-image-2 treats *only* alpha == 0 as "edit". Any
// intermediate value (e.g. 38 from a 0.85-opacity brush stroke) is
// rounded UP to "preserve", so the model sees no edit region at all
// and silently ignores the mask — producing a full-canvas
// regeneration that keeps part of the input's composition. We hit
// this in real-API tests; see the bug audit in PR #92.
//
// Therefore: BINARIZE the alpha at export time. The brush's soft
// edge / spatial feathering already happens upstream of us (the
// brush stroke produced rgba(...,0.85) in the painted area). Here
// we just collapse to a hard 0/255 mask before sending to the API.
const PAINT_THRESHOLD = 16;

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
    // Binary inversion: any visibly-painted pixel becomes alpha=0
    // (edit), every other pixel stays alpha=255 (preserve). Without
    // this hard threshold gpt-image-2 ignores the mask entirely.
    out.data[i + 3] = src.data[i + 3] > PAINT_THRESHOLD ? 0 : 255;
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
