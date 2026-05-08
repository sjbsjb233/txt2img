import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import MEIcon from "./MEIcon.jsx";
import { screenToImage, computeFitTransform } from "./utils/coords.js";
import { classifyWheel } from "./utils/gestures.js";
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

const CanvasStage = forwardRef(function CanvasStage({
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
  onBrushDelta,
  onZoomChange,
  onHudMessage,
}, ref) {
  const containerRef = useRef(null);
  const sourceRef = useRef(null);
  const maskRef = useRef(null);
  const interactionRef = useRef(null);

  const [transform, setTransform] = useState({
    scale: 1,
    translateX: 0,
    translateY: 0,
  });
  const transformRef = useRef(transform);
  useEffect(() => {
    transformRef.current = transform;
  }, [transform]);

  const [cursorPos, setCursorPos] = useState(null); // screen coords
  const [containerSize, setContainerSize] = useState({ w: 1, h: 1 });
  const containerSizeRef = useRef(containerSize);
  useEffect(() => {
    containerSizeRef.current = containerSize;
  }, [containerSize]);
  const toolRef = useRef(null);
  const panActiveRef = useRef(null);

  // Inertia bookkeeping for two-finger pan momentum.
  const inertiaVxRef = useRef(0);
  const inertiaVyRef = useRef(0);
  const inertiaLastWheelRef = useRef(0);
  const inertiaRafRef = useRef(null);
  const spacePressedRef = useRef(false);

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

  // Notify parent whenever zoom changes so the status bar stays in sync.
  useEffect(() => {
    onZoomChange?.(Math.round(transform.scale * 100));
  }, [transform.scale, onZoomChange]);

  // === Native event listeners (gesture blocking + Space) ===================
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const block = (e) => e.preventDefault();
    el.addEventListener("gesturestart", block);
    el.addEventListener("gesturechange", block);
    el.addEventListener("gestureend", block);
    el.addEventListener("contextmenu", block);
    el.addEventListener("dblclick", block);
    return () => {
      el.removeEventListener("gesturestart", block);
      el.removeEventListener("gesturechange", block);
      el.removeEventListener("gestureend", block);
      el.removeEventListener("contextmenu", block);
      el.removeEventListener("dblclick", block);
    };
  }, []);

  // Space-to-pan: holding Space temporarily switches to the pan tool
  // without disturbing the user's selected tool. We listen on window so
  // the canvas doesn't need focus; we still ignore the keystroke when
  // the user is typing into a text field.
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.code !== "Space" || e.repeat) return;
      const tag = document.activeElement?.tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") return;
      spacePressedRef.current = true;
      e.preventDefault();
      containerRef.current?.classList.add("me-stage--pan");
    };
    const onKeyUp = (e) => {
      if (e.code !== "Space") return;
      spacePressedRef.current = false;
      containerRef.current?.classList.remove("me-stage--pan");
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);

  // Inertia kick-off: after the wheel stream goes quiet for ~80ms we
  // start a decay loop using the last observed pan velocity. The check
  // also respects `prefers-reduced-motion`.
  useEffect(() => {
    const id = setInterval(() => {
      if (
        inertiaLastWheelRef.current > 0 &&
        performance.now() - inertiaLastWheelRef.current > 80 &&
        !inertiaRafRef.current &&
        (Math.abs(inertiaVxRef.current) > 0.5 || Math.abs(inertiaVyRef.current) > 0.5)
      ) {
        inertiaLastWheelRef.current = 0;
        startInertia();
      }
    }, 50);
    return () => clearInterval(id);
  }, []);

  function cancelInertia() {
    if (inertiaRafRef.current) {
      cancelAnimationFrame(inertiaRafRef.current);
      inertiaRafRef.current = null;
    }
  }

  function startInertia() {
    if (
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      inertiaVxRef.current = 0;
      inertiaVyRef.current = 0;
      return;
    }
    cancelInertia();
    const tick = () => {
      inertiaVxRef.current *= 0.92;
      inertiaVyRef.current *= 0.92;
      if (
        Math.abs(inertiaVxRef.current) < 0.2 &&
        Math.abs(inertiaVyRef.current) < 0.2
      ) {
        inertiaVxRef.current = 0;
        inertiaVyRef.current = 0;
        inertiaRafRef.current = null;
        return;
      }
      const vx = inertiaVxRef.current;
      const vy = inertiaVyRef.current;
      setTransform((t) => ({
        ...t,
        translateX: t.translateX + vx,
        translateY: t.translateY + vy,
      }));
      inertiaRafRef.current = requestAnimationFrame(tick);
    };
    inertiaRafRef.current = requestAnimationFrame(tick);
  }

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
    if (tool === "pan" || e.button === 1 || spacePressedRef.current) {
      cancelInertia();
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
    const opts = brushOpts.pressure
      ? { ...brushOpts, _pressure: e.pressure || 0.5 }
      : brushOpts;
    t.onPointerDown(mCtx, pt, opts);
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
    const mCtx = maskRef.current.getContext("2d");
    // Use coalesced events so 120Hz trackpads / drawing tablets can
    // contribute every raw sample to the stroke instead of getting
    // lossily down-sampled to React's pointer events.
    const native = e.nativeEvent;
    const events = native && typeof native.getCoalescedEvents === "function"
      ? native.getCoalescedEvents()
      : [native || e];
    const list = events && events.length ? events : [native || e];
    const containerRect = e.currentTarget.getBoundingClientRect();
    for (const ev of list) {
      const evx = (ev.clientX ?? sx + containerRect.left) - containerRect.left;
      const evy = (ev.clientY ?? sy + containerRect.top) - containerRect.top;
      const pt = screenToImage(evx, evy, transform);
      const opts = brushOpts.pressure
        ? { ...brushOpts, _pressure: typeof ev.pressure === "number" ? ev.pressure : 0.5 }
        : brushOpts;
      t.onPointerMove(mCtx, pt, opts);
    }
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

  // Wheel: dispatch to zoom / pan / brush parameter handlers depending
  // on the modifier state. macOS pinch arrives as ctrlKey + wheel.
  const onWheel = (e) => {
    e.preventDefault();
    const gesture = classifyWheel(e.nativeEvent || e);
    const rect = containerRef.current.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;

    if (gesture.kind === "zoom") {
      cancelInertia();
      inertiaVxRef.current = 0;
      inertiaVyRef.current = 0;
      const newScale = Math.max(0.1, Math.min(8, transform.scale * gesture.factor));
      const before = screenToImage(sx, sy, transform);
      const next = {
        scale: newScale,
        translateX: sx - before.x * newScale,
        translateY: sy - before.y * newScale,
      };
      setTransform(next);
      onHudMessage?.(`zoom ${Math.round(newScale * 100)}%`);
      return;
    }

    if (gesture.kind === "pan") {
      cancelInertia();
      setTransform((t) => ({
        ...t,
        translateX: t.translateX + gesture.dx,
        translateY: t.translateY + gesture.dy,
      }));
      inertiaVxRef.current = gesture.dx;
      inertiaVyRef.current = gesture.dy;
      inertiaLastWheelRef.current = performance.now();
      return;
    }

    if (gesture.kind === "brushSize") {
      onBrushDelta?.({ kind: "size", delta: gesture.dy });
      return;
    }
    if (gesture.kind === "hardness") {
      onBrushDelta?.({ kind: "hardness", delta: gesture.dy });
      return;
    }
    if (gesture.kind === "opacity") {
      onBrushDelta?.({ kind: "opacity", delta: gesture.dy });
      return;
    }
  };

  // Imperative API for parent-level keyboard shortcuts (Cmd+0/1/+/-).
  function fit() {
    const cs = containerSizeRef.current;
    if (!imageW || !imageH || !cs.w || !cs.h) return;
    cancelInertia();
    const t = computeFitTransform(imageW, imageH, cs.w, cs.h);
    setTransform(t);
    onHudMessage?.(`zoom ${Math.round(t.scale * 100)}%`);
  }
  function actual() {
    const cs = containerSizeRef.current;
    cancelInertia();
    const cx = cs.w / 2;
    const cy = cs.h / 2;
    const t = transformRef.current;
    const before = screenToImage(cx, cy, t);
    setTransform({
      scale: 1,
      translateX: cx - before.x * 1,
      translateY: cy - before.y * 1,
    });
    onHudMessage?.("zoom 100%");
  }
  function zoomBy(factor) {
    cancelInertia();
    const cs = containerSizeRef.current;
    const cx = cs.w / 2;
    const cy = cs.h / 2;
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
  useImperativeHandle(ref, () => ({
    fit,
    actual,
    zoomBy,
  }));

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
        onFit={fit}
      />
    </div>
  );
});

export default CanvasStage;

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
