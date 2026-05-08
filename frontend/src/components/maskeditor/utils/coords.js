// Image <-> screen coordinate transforms.
// All "image space" measurements (brush radius, expand radius, etc.)
// stay in original image pixels — that way a 28px brush at 200% zoom
// looks like 56 screen pixels but writes 28 image pixels. That keeps
// the user's mental model stable across zooms.

export function screenToImage(sx, sy, transform) {
  return {
    x: (sx - transform.translateX) / transform.scale,
    y: (sy - transform.translateY) / transform.scale,
  };
}

export function imageToScreen(ix, iy, transform) {
  return {
    x: ix * transform.scale + transform.translateX,
    y: iy * transform.scale + transform.translateY,
  };
}

// Compute the centered fit transform for an image of (iw, ih) inside
// a viewport of (vw, vh). Returns ``{scale, translateX, translateY}``
// where the image occupies as much of the viewport as possible while
// keeping aspect ratio.
export function computeFitTransform(iw, ih, vw, vh) {
  const padding = 32; // breathing room on each side
  const innerW = Math.max(1, vw - padding * 2);
  const innerH = Math.max(1, vh - padding * 2);
  const scale = Math.min(innerW / iw, innerH / ih, 8);
  const cw = iw * scale;
  const ch = ih * scale;
  return {
    scale,
    translateX: Math.round((vw - cw) / 2),
    translateY: Math.round((vh - ch) / 2),
  };
}
