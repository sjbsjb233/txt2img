// Browser-side diff/heatmap analysis for mask-edit compare mode.
//
// Inspired by backend ``provider_test_runner._inpaint_metrics``: same
// intent (split source-vs-result diff into preserve vs edit regions,
// report a per-region indicator + ratio) and the same DIFF_THRESHOLD
// noise floor, but a *different statistic* — backend reports the
// region's mean diff magnitude normalised to 0..1 (``preserve_score``
// / ``edit_score``) and gates on 0.05 / 0.08; we report % of pixels
// above the noise floor (``preserve_change_pct`` / ``edit_change_pct``)
// and gate on 5% / 15%. The two are well-correlated in practice but
// not interchangeable, so don't compare admin sub-verdicts against
// these numbers byte-for-byte — they're a UX guide for users, not a
// re-implementation of the admin metric.
//
// Three numbers, all 0..100:
//   - preserve_change_pct  — fraction of preserve-region pixels whose
//     L1 difference between source and result is over DIFF_THRESHOLD.
//     Lower is better; this is the spill.
//   - edit_change_pct      — same metric, in the edit region. Higher
//     is better; tells the user the model actually did something.
//   - ratio                — edit / preserve. Higher is cleaner.
//
// Plus a heatmap PNG blob (RGBA) you can ``URL.createObjectURL`` into
// an <img>; it overlays the compare surface with mix-blend-mode:
// multiply. Pixels below DIFF_THRESHOLD are fully transparent so the
// overlay reads as "where did the model actually change things",
// matching the backend ``_make_diff_heatmap`` semantics.

const DIFF_THRESHOLD = 13;  // 0..255; ~5% per-channel grey change

function isCanvasLike(el) {
  return !!el && typeof el.getContext === "function";
}

async function bitmapToImageData(bitmap, w, h) {
  const off = document.createElement("canvas");
  off.width = w;
  off.height = h;
  const ctx = off.getContext("2d", { willReadFrequently: true });
  if (typeof bitmap === "string") {
    // URL — load via Image.
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.crossOrigin = "anonymous";
      i.onload = () => resolve(i);
      i.onerror = (e) => reject(e);
      i.src = bitmap;
    });
    ctx.drawImage(img, 0, 0, w, h);
  } else if (isCanvasLike(bitmap)) {
    ctx.drawImage(bitmap, 0, 0, w, h);
  } else {
    ctx.drawImage(bitmap, 0, 0, w, h);
  }
  return ctx.getImageData(0, 0, w, h);
}

/**
 * Analyse mask-edit results against the source image.
 *
 * @param {Object} args
 * @param {*} args.source   ImageBitmap / HTMLImage / canvas — before image
 * @param {*} args.result   ImageBitmap / HTMLImage / canvas — after image
 * @param {*} args.mask     painted RGBA canvas (alpha > threshold = edit)
 * @param {number} [args.size] downscale longest edge to this (default 512)
 * @returns {Promise<{
 *   metrics: {
 *     preserve_change_pct: number,
 *     edit_change_pct: number,
 *     ratio: number,
 *     preserve_px: number,
 *     edit_px: number,
 *   },
 *   tier: "ok" | "warn" | "danger",
 *   heatmapBlob: Blob,
 *   heatmapW: number,
 *   heatmapH: number,
 * }>}
 */
export async function analyzeMaskEdit({ source, result, mask, size = 512 }) {
  // Pick a working size. Bounding to ~512px keeps the loop snappy on
  // a 4K result (~80ms vs ~2s).
  const srcW = source.width;
  const srcH = source.height;
  const scale = Math.min(1, size / Math.max(srcW, srcH));
  const w = Math.max(1, Math.round(srcW * scale));
  const h = Math.max(1, Math.round(srcH * scale));

  const [srcData, resData, maskData] = await Promise.all([
    bitmapToImageData(source, w, h),
    bitmapToImageData(result, w, h),
    bitmapToImageData(mask, w, h),
  ]);

  const src = srcData.data;
  const res = resData.data;
  const msk = maskData.data;

  // Heatmap buffer (RGBA). We render a per-pixel jet-ish ramp on the
  // diff magnitude. Multiply-blended over the image so 0-diff pixels
  // stay invisible.
  const heat = new Uint8ClampedArray(w * h * 4);

  let preserveTotal = 0;
  let preserveChanged = 0;
  let editTotal = 0;
  let editChanged = 0;

  for (let i = 0; i < src.length; i += 4) {
    // L1 distance in RGB, averaged.
    const d =
      (Math.abs(src[i] - res[i]) +
        Math.abs(src[i + 1] - res[i + 1]) +
        Math.abs(src[i + 2] - res[i + 2])) /
      3;
    const isEdit = msk[i + 3] > 16;
    if (isEdit) {
      editTotal += 1;
      if (d > DIFF_THRESHOLD) editChanged += 1;
    } else {
      preserveTotal += 1;
      if (d > DIFF_THRESHOLD) preserveChanged += 1;
    }
    // Heatmap: transparent below DIFF_THRESHOLD (matches backend
    // ``_make_diff_heatmap`` so admin diagnostics and user-facing
    // overlay highlight the same pixels), then ramp blue → cyan →
    // green → yellow → red over the visible range.
    if (d < DIFF_THRESHOLD) {
      heat[i + 3] = 0;
    } else {
      // 0..1 over the visible range [DIFF_THRESHOLD, DIFF_THRESHOLD + 96].
      // We cap at +96 (≈37% per-channel grey) rather than 255 so a
      // visible-but-modest change already saturates to red — admin's
      // 0..255 ramp washes out for typical inpaint deltas.
      const t = Math.min(1, (d - DIFF_THRESHOLD) / 96);
      let r, g, b;
      if (t < 0.25) {
        const k = t / 0.25;
        r = 43 + (45 - 43) * k;
        g = 79 + (177 - 79) * k;
        b = 138 + (197 - 138) * k;
      } else if (t < 0.5) {
        const k = (t - 0.25) / 0.25;
        r = 45 + (78 - 45) * k;
        g = 177 + (168 - 177) * k;
        b = 197 + (58 - 197) * k;
      } else if (t < 0.75) {
        const k = (t - 0.5) / 0.25;
        r = 78 + (244 - 78) * k;
        g = 168 + (196 - 168) * k;
        b = 58 + (48 - 58) * k;
      } else {
        const k = (t - 0.75) / 0.25;
        r = 244 + (197 - 244) * k;
        g = 196 + (52 - 196) * k;
        b = 48 + (58 - 48) * k;
      }
      heat[i] = r;
      heat[i + 1] = g;
      heat[i + 2] = b;
      // Boost spill pixels (outside mask, big diff) so they pop
      // visually even at lower opacity.
      heat[i + 3] = Math.min(255, 80 + t * 175);
    }
  }

  const preservePct = preserveTotal > 0 ? (preserveChanged / preserveTotal) * 100 : 0;
  const editPct = editTotal > 0 ? (editChanged / editTotal) * 100 : 0;
  const ratio = preservePct > 0.01 ? editPct / preservePct : (editPct > 0 ? 999 : 0);

  const heatCanvas = document.createElement("canvas");
  heatCanvas.width = w;
  heatCanvas.height = h;
  const heatCtx = heatCanvas.getContext("2d");
  const heatImage = new ImageData(heat, w, h);
  heatCtx.putImageData(heatImage, 0, 0);
  const heatmapBlob = await new Promise((resolve, reject) => {
    heatCanvas.toBlob((b) => (b ? resolve(b) : reject(new Error("heatmap toBlob failed"))), "image/png");
  });

  const tier = preservePct >= 15 ? "danger" : preservePct >= 5 ? "warn" : "ok";

  return {
    metrics: {
      preserve_change_pct: round1(preservePct),
      edit_change_pct: round1(editPct),
      ratio: round1(ratio),
      preserve_px: preserveTotal,
      edit_px: editTotal,
    },
    tier,
    heatmapBlob,
    heatmapW: w,
    heatmapH: h,
  };
}

function round1(n) {
  if (!Number.isFinite(n)) return n;
  return Math.round(n * 10) / 10;
}
