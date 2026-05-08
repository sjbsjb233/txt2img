import { useEffect, useLayoutEffect, useRef, useState } from "react";
import MEIcon from "./MEIcon.jsx";
import { screenToImage, computeFitTransform } from "./utils/coords.js";
import { createBrushTool } from "./tools/BrushTool.js";
import { createRectTool } from "./tools/RectTool.js";
import { createLassoTool } from "./tools/LassoTool.js";
import { createMagicWandTool } from "./tools/MagicWandTool.js";

// One Canvas stage manages 3 stacked canvases:
// - source (raster of the original image, never modified)
// - mask (user paint, RGBA — alpha is the signal)
// - interaction (live preview of in-flight strokes / selection box)
//
// We expose imperative refs (sourceCanvas, maskCanvas) via the
// onReady callback so the parent can run mask ops + history capture
// without re-rendering the canvas.

export default function CanvasStage({
  imageBitmap,
  imageW,
  imageH,
  tool,
  brushOpts,
  outpaintMode = false,
  outpaintGeometry = null, // {left, right, top, bottom} in image px
  onCursorChange,
  onMaskChange,
  onReady,
}) {
  const containerRef = useRef(null);
  const sourceRef = useRef(null);
  const maskRef = useRef(null);
  const interactionRef = useRef(null);

  const [transform, setTransform] = useState({
    scale: 1,
    translateX: 0,
    translateY: 0,
  });
  const [cursorPos, setCursorPos] = useState(null); // screen coords
  const [containerSize, setContainerSize] = useState({ w: 1, h: 1 });
  const toolRef = useRef(null);
  const panActiveRef = useRef(null);

  // Recreate the tool when the active tool id changes.
  useEffect(() => {
    if (tool === "brush") toolRef.current = createBrushTool();
    else if (tool === "eraser") toolRef.current = createBrushTool({ erase: true });
    else if (tool === "rect") toolRef.current = createRectTool();
    else if (tool === "lasso") toolRef.current = createLassoTool();
    else if (tool === "wand") toolRef.current = createMagicWandTool();
    else toolRef.current = null;
  }, [tool]);

  // Initial container size measurement, plus on resize.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const update = () => {
      const rect = el.getBoundingClientRect();
      setContainerSize({ w: rect.width, h: rect.height });
    };
    update();
    const obs = new ResizeObserver(update);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Initialise source/mask canvases when image arrives.
  useEffect(() => {
    if (!imageBitmap) return;
    const sCanvas = sourceRef.current;
    const mCanvas = maskRef.current;
    const iCanvas = interactionRef.current;
    if (!sCanvas || !mCanvas || !iCanvas) return;
    sCanvas.width = imageW;
    sCanvas.height = imageH;
    mCanvas.width = imageW;
    mCanvas.height = imageH;
    iCanvas.width = imageW;
    iCanvas.height = imageH;
    const sCtx = sCanvas.getContext("2d");
    sCtx.clearRect(0, 0, imageW, imageH);
    sCtx.drawImage(imageBitmap, 0, 0, imageW, imageH);
    const mCtx = mCanvas.getContext("2d");
    mCtx.clearRect(0, 0, imageW, imageH);
    if (onReady) {
      onReady({
        sourceCanvas: sCanvas,
        maskCanvas: mCanvas,
        interactionCanvas: iCanvas,
      });
    }
  }, [imageBitmap, imageW, imageH, onReady]);

  // Initial fit transform once both image + container ready.
  useEffect(() => {
    if (!imageW || !imageH || !containerSize.w || !containerSize.h) return;
    const t = computeFitTransform(imageW, imageH, containerSize.w, containerSize.h);
    setTransform(t);
  }, [imageW, imageH, containerSize.w, containerSize.h]);

  // === Pointer events ====================================================

  const isPaintingRef = useRef(false);

  const onPointerDown = (e) => {
    if (!imageBitmap) return;
    // 让 ZoomWidget / Rulers 等子元素不被画布捕获指针
    if (e.target.closest(".me-zoom") || e.target.closest(".me-ruler")) {
      return;
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    if (tool === "pan" || e.button === 1 || e.shiftKey === false && tool === "pan") {
      panActiveRef.current = { sx, sy, base: { ...transform } };
      return;
    }
    const pt = screenToImage(sx, sy, transform);
    const t = toolRef.current;
    if (!t) return;
    isPaintingRef.current = true;
    const mCtx = maskRef.current.getContext("2d");
    if (tool === "wand") {
      t.onPointerDown(mCtx, pt, {
        sourceCanvas: sourceRef.current,
        tolerance: brushOpts.tolerance ?? 32,
      });
      onMaskChange?.({ kind: tool, label: `${tool} · click` });
      isPaintingRef.current = false;
      return;
    }
    t.onPointerDown(mCtx, pt, brushOpts);
    drawInteractionPreview();
  };

  const onPointerMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    setCursorPos({ x: sx, y: sy });
    if (onCursorChange) {
      const pt = screenToImage(sx, sy, transform);
      onCursorChange({
        screenX: sx, screenY: sy,
        imageX: Math.round(pt.x), imageY: Math.round(pt.y),
      });
    }
    if (panActiveRef.current) {
      const dx = sx - panActiveRef.current.sx;
      const dy = sy - panActiveRef.current.sy;
      setTransform({
        ...transform,
        translateX: panActiveRef.current.base.translateX + dx,
        translateY: panActiveRef.current.base.translateY + dy,
      });
      return;
    }
    if (!isPaintingRef.current) return;
    const t = toolRef.current;
    if (!t) return;
    const pt = screenToImage(sx, sy, transform);
    const mCtx = maskRef.current.getContext("2d");
    t.onPointerMove(mCtx, pt, brushOpts);
    drawInteractionPreview();
  };

  const onPointerUp = (e) => {
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (panActiveRef.current) {
      panActiveRef.current = null;
      return;
    }
    if (!isPaintingRef.current) return;
    isPaintingRef.current = false;
    const t = toolRef.current;
    if (!t) return;
    const mCtx = maskRef.current.getContext("2d");
    const wasActive = t.onPointerUp(mCtx);
    clearInteraction();
    if (wasActive) {
      onMaskChange?.({
        kind: tool,
        label: `${tool} · stroke`,
      });
    }
  };

  function drawInteractionPreview() {
    const t = toolRef.current;
    if (!t) return;
    const iCanvas = interactionRef.current;
    if (!iCanvas) return;
    const ctx = iCanvas.getContext("2d");
    ctx.clearRect(0, 0, iCanvas.width, iCanvas.height);
    if (tool === "rect" && t.getPreview) {
      const r = t.getPreview();
      if (r) {
        ctx.save();
        ctx.strokeStyle = "rgba(0,0,0,0.9)";
        ctx.setLineDash([4, 2]);
        ctx.lineWidth = 1;
        ctx.strokeRect(r.x, r.y, r.w, r.h);
        ctx.restore();
      }
    } else if (tool === "lasso" && t.getPreview) {
      const pts = t.getPreview();
      if (pts && pts.length > 1) {
        ctx.save();
        ctx.strokeStyle = "rgba(0,0,0,0.9)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
        ctx.stroke();
        ctx.restore();
      }
    }
  }
  function clearInteraction() {
    const iCanvas = interactionRef.current;
    if (!iCanvas) return;
    const ctx = iCanvas.getContext("2d");
    ctx.clearRect(0, 0, iCanvas.width, iCanvas.height);
  }

  // Wheel zoom
  const onWheel = (e) => {
    e.preventDefault();
    const rect = containerRef.current.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const delta = -Math.sign(e.deltaY);
    const factor = delta > 0 ? 1.1 : 1 / 1.1;
    const newScale = Math.max(0.1, Math.min(8, transform.scale * factor));
    // Keep the point under cursor stationary.
    const before = screenToImage(sx, sy, transform);
    const newTx = sx - before.x * newScale;
    const newTy = sy - before.y * newScale;
    setTransform({ scale: newScale, translateX: newTx, translateY: newTy });
  };

  // Frame styles
  const cw = imageW * transform.scale;
  const ch = imageH * transform.scale;
  const frameStyle = {
    left: transform.translateX,
    top: transform.translateY,
    width: cw,
    height: ch,
  };

  const stageClass = [
    "me-stage",
    tool === "brush" || tool === "eraser" ? "me-stage--brush" : "",
    tool === "pan" ? (panActiveRef.current ? "me-stage--pan-active" : "me-stage--pan") : "",
  ].filter(Boolean).join(" ");

  return (
    <div
      ref={containerRef}
      className={stageClass}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onWheel={onWheel}
      data-testid="me-stage"
    >
      {/* Outpaint bleed area */}
      {outpaintMode && outpaintGeometry && (
        <div
          className="me-outpaint-bleed"
          style={{
            left: transform.translateX - (outpaintGeometry.left || 0) * transform.scale,
            top: transform.translateY - (outpaintGeometry.top || 0) * transform.scale,
            width: cw + ((outpaintGeometry.left || 0) + (outpaintGeometry.right || 0)) * transform.scale,
            height: ch + ((outpaintGeometry.top || 0) + (outpaintGeometry.bottom || 0)) * transform.scale,
          }}
        />
      )}
      <div className="me-image-frame" style={frameStyle}>
        <canvas
          ref={sourceRef}
          className="me-canvas-layer"
          style={{ zIndex: 1 }}
          data-testid="me-canvas-source"
        />
        <canvas
          ref={maskRef}
          className="me-canvas-layer"
          style={{ zIndex: 2, opacity: 0.6 }}
          data-testid="me-canvas-mask"
        />
        <canvas
          ref={interactionRef}
          className="me-canvas-layer"
          style={{ zIndex: 3 }}
          data-testid="me-canvas-interaction"
        />
      </div>
      {(tool === "brush" || tool === "eraser") && cursorPos && (
        <div
          data-testid="me-cursor-ring"
          className="me-cursor-ring"
          style={{
            left: cursorPos.x - (brushOpts.size * transform.scale) / 2,
            top: cursorPos.y - (brushOpts.size * transform.scale) / 2,
            width: brushOpts.size * transform.scale,
            height: brushOpts.size * transform.scale,
          }}
        />
      )}
      <Rulers
        width={containerSize.w}
        height={containerSize.h}
        offX={transform.translateX}
        offY={transform.translateY}
        zoom={transform.scale}
        imageW={imageW}
        imageH={imageH}
      />
      <ZoomWidget
        zoom={Math.round(transform.scale * 100)}
        onZoomIn={() => zoomBy(1.25)}
        onZoomOut={() => zoomBy(0.8)}
        onFit={() => {
          const t = computeFitTransform(imageW, imageH, containerSize.w, containerSize.h);
          setTransform(t);
        }}
      />
    </div>
  );

  function zoomBy(factor) {
    const cx = containerSize.w / 2;
    const cy = containerSize.h / 2;
    setTransform((t) => {
      const newScale = Math.max(0.1, Math.min(8, t.scale * factor));
      const before = screenToImage(cx, cy, t);
      return {
        scale: newScale,
        translateX: cx - before.x * newScale,
        translateY: cy - before.y * newScale,
      };
    });
  }
}

function Rulers({ width, height, offX, offY, zoom, imageW, imageH }) {
  if (!width || !height) return null;
  const xTicks = [];
  for (let x = 0; x <= imageW; x += 100) {
    const sx = offX + x * zoom;
    if (sx >= 16 && sx <= width) xTicks.push({ pos: sx, label: x });
  }
  const yTicks = [];
  for (let y = 0; y <= imageH; y += 100) {
    const sy = offY + y * zoom;
    if (sy >= 16 && sy <= height) yTicks.push({ pos: sy, label: y });
  }
  return (
    <>
      <div className="me-ruler me-ruler--corner" />
      <div className="me-ruler me-ruler--x">
        {xTicks.map((t, i) => (
          <div key={i} style={{ position: "absolute", left: t.pos - 16, top: 0, height: "100%" }}>
            <div className="me-ruler__tick" style={{ left: 0, top: 8, height: 8, width: 1 }} />
            <span className="me-ruler__label" style={{ left: 2, top: -1 }}>{t.label}</span>
          </div>
        ))}
      </div>
      <div className="me-ruler me-ruler--y">
        {yTicks.map((t, i) => (
          <div key={i} style={{ position: "absolute", top: t.pos - 16, left: 0, width: "100%" }}>
            <div className="me-ruler__tick" style={{ top: 0, left: 8, width: 8, height: 1 }} />
            <span className="me-ruler__label" style={{ top: 1, left: 1, writingMode: "vertical-rl" }}>{t.label}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function ZoomWidget({ zoom, onZoomIn, onZoomOut, onFit }) {
  return (
    <div className="me-zoom" data-testid="me-zoom">
      <button className="me-zoom__btn" onClick={onZoomOut} title="Zoom out" data-testid="me-zoom-out">
        <MEIcon name="zoom-out" size={14} />
      </button>
      <div className="me-zoom__pct" data-testid="me-zoom-pct">{zoom}%</div>
      <button className="me-zoom__btn" onClick={onZoomIn} title="Zoom in" data-testid="me-zoom-in">
        <MEIcon name="zoom-in" size={14} />
      </button>
      <button className="me-zoom__btn" onClick={onFit} title="Fit to screen" data-testid="me-zoom-fit">
        <MEIcon name="fit" size={14} />
      </button>
    </div>
  );
}
