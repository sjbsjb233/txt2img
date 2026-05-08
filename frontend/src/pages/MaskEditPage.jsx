// Mask editor takeover page (route /edit/:hashId/:order).
// See frontend §5 — full-screen takeover, not a modal.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import TopBar from "../components/maskeditor/TopBar.jsx";
import StatusBar from "../components/maskeditor/StatusBar.jsx";
import Toolbar from "../components/maskeditor/Toolbar.jsx";
import CanvasStage from "../components/maskeditor/CanvasStage.jsx";
import RightPanel from "../components/maskeditor/RightPanel/index.jsx";
import ErrorModal from "../components/maskeditor/ErrorModal.jsx";
import ValidationBanner from "../components/maskeditor/ValidationBanner.jsx";
import StreamingOverlay from "../components/maskeditor/StreamingOverlay.jsx";
import CheatSheetOverlay from "../components/maskeditor/CheatSheetOverlay.jsx";
import CompareSurface from "../components/maskeditor/CompareSurface.jsx";
import CompareRightPanel from "../components/maskeditor/CompareRightPanel.jsx";
import HudToast from "../components/maskeditor/HudToast.jsx";

import { getJob, imageOriginalUrl, fetchImageBlob } from "../api/archive.js";
import { createJob } from "../api/jobs.js";
import { getDerivedJobs } from "../api/derived.js";
import { supportsMaskEdit, markMaskEditUsed } from "../config/maskEdit.js";
import { createHistoryStack } from "../components/maskeditor/history/HistoryStack.js";
import {
  applyAll, applyClear, applyInvert, applyFeather, applyExpand,
  applyContract, applySmooth,
} from "../components/maskeditor/ops/maskOps.js";
import { exportMaskPng, countMaskPaintedPixels, maskPaintedRatio } from "../components/maskeditor/utils/maskExport.js";

const MIN_MASK_PIXELS = 100;
const MAX_MASK_RATIO = 0.95;

const DEFAULT_BRUSH = {
  size: 28,
  hardness: 80,
  opacity: 100,
  spacing: 10,
  smoothing: true,
  pressure: true,
  tolerance: 32,
};

const DEFAULT_ADVANCED = {
  size: "auto",
  quality: "medium",
  thinking: "off",
  output: "png",
  streamPartial: false,
};

export default function MaskEditPage() {
  const { hashId, order } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [sourceJob, setSourceJob] = useState(null);
  const [sourceImage, setSourceImage] = useState(null); // ImageBitmap
  const [sourceImageUrl, setSourceImageUrl] = useState(null);
  const [imageW, setImageW] = useState(1024);
  const [imageH, setImageH] = useState(1024);
  const [tool, setTool] = useState("brush");
  const [tab, setTab] = useState("tool");
  const [brushOpts, setBrushOpts] = useState(DEFAULT_BRUSH);
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("no people, no text, no logos");
  const [refs, setRefs] = useState([]);
  const [advanced, setAdvanced] = useState(DEFAULT_ADVANCED);
  const [history, setHistory] = useState([]);
  const [lastOp, setLastOp] = useState(null);
  const [status, setStatus] = useState("idle"); // idle/submitting/running/done/error/empty
  const [errorState, setErrorState] = useState(null); // {code, message}
  const [showCheat, setShowCheat] = useState(false);
  const [maskCanvasRef, setMaskCanvasRef] = useState(null);
  const [sourceCanvasRef, setSourceCanvasRef] = useState(null);
  const [statusHint, setStatusHint] = useState("paint a mask area before submitting (≥ 100 px)");
  const [zoomDisplay, setZoomDisplay] = useState(100);
  const [cursorXY, setCursorXY] = useState({ x: 0, y: 0 });
  const [outpaintMode, setOutpaintMode] = useState(searchParams.get("mode") === "outpaint");
  const [outpaint, setOutpaint] = useState({
    directions: ["right"],
    amount: "25%",
  });
  const [resultJob, setResultJob] = useState(null);
  const [resultUrl, setResultUrl] = useState(null);
  const [derivedVersions, setDerivedVersions] = useState([]);
  const [streaming, setStreaming] = useState(null);
  const [hud, setHud] = useState(null);

  const historyStackRef = useRef(null);
  const canvasStageRef = useRef(null);
  const hudTimerRef = useRef(null);

  function showHud(text) {
    setHud(text);
    if (hudTimerRef.current) clearTimeout(hudTimerRef.current);
    hudTimerRef.current = setTimeout(() => setHud(null), 1200);
  }

  useEffect(() => () => {
    if (hudTimerRef.current) clearTimeout(hudTimerRef.current);
  }, []);

  // Load the source job + image bitmap.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const job = await getJob(hashId);
        if (cancelled) return;
        if (!supportsMaskEdit(job.model)) {
          alert("This model doesn't support mask editing.");
          navigate("/archive");
          return;
        }
        setSourceJob(job);
        if (job.prompt) setPrompt(job.prompt);
        const requestedOrder = parseInt(order, 10) || 1;
        const img = (job.images || []).find((i) => i.order === requestedOrder);
        if (!img) {
          alert("Image not found for this order.");
          navigate("/archive");
          return;
        }
        const url = imageOriginalUrl(job.hash_id, img.order);
        const blob = await fetchImageBlob(url);
        if (!blob) {
          alert("Source image is no longer available.");
          navigate("/archive");
          return;
        }
        const bmp = await createImageBitmap(blob);
        if (cancelled) return;
        setImageW(bmp.width);
        setImageH(bmp.height);
        setSourceImage(bmp);
        setSourceImageUrl(URL.createObjectURL(blob));
      } catch (e) {
        console.error("Failed to load source:", e);
        alert(`Could not open editor: ${e.message || e}`);
        navigate("/archive");
      }
    })();
    return () => { cancelled = true; };
  }, [hashId, order, navigate]);

  // Initialise history stack once mask canvas is available.
  const onCanvasReady = useCallback(({ sourceCanvas, maskCanvas }) => {
    setSourceCanvasRef(sourceCanvas);
    setMaskCanvasRef(maskCanvas);
    if (!historyStackRef.current && maskCanvas) {
      const ctx = maskCanvas.getContext("2d");
      const snap = ctx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
      historyStackRef.current = createHistoryStack(snap, { kind: "init", label: "open source" });
      setHistory(historyStackRef.current.getList());
    }
  }, []);

  function captureSnapshot(meta) {
    if (!historyStackRef.current || !maskCanvasRef) return;
    const ctx = maskCanvasRef.getContext("2d");
    const snap = ctx.getImageData(0, 0, maskCanvasRef.width, maskCanvasRef.height);
    historyStackRef.current.push(snap, meta);
    setHistory(historyStackRef.current.getList());
  }

  function applyOp(opId, value) {
    if (!maskCanvasRef) return;
    if (opId === "all") applyAll(maskCanvasRef);
    else if (opId === "none" || opId === "trash") applyClear(maskCanvasRef);
    else if (opId === "invert") applyInvert(maskCanvasRef);
    else if (opId === "feather") applyFeather(maskCanvasRef, value || 6);
    else if (opId === "expand") applyExpand(maskCanvasRef, value || 5);
    else if (opId === "contract") applyContract(maskCanvasRef, value || 5);
    else if (opId === "smooth") applySmooth(maskCanvasRef, value || 3);
    setLastOp(opId);
    captureSnapshot({ kind: opId, label: `${opId}${value ? ` · ${value}` : ""}` });
  }

  function onMaskChange({ kind, label }) {
    captureSnapshot({ kind, label });
  }

  function jumpHistory(idx) {
    if (!historyStackRef.current || !maskCanvasRef) return;
    const step = historyStackRef.current.jumpTo(idx);
    if (!step) return;
    const ctx = maskCanvasRef.getContext("2d");
    ctx.putImageData(step.snapshot, 0, 0);
    setHistory(historyStackRef.current.getList());
  }
  function undo() {
    if (!historyStackRef.current || !maskCanvasRef) return;
    const step = historyStackRef.current.undo();
    if (!step) return;
    const ctx = maskCanvasRef.getContext("2d");
    ctx.putImageData(step.snapshot, 0, 0);
    setHistory(historyStackRef.current.getList());
  }
  function redo() {
    if (!historyStackRef.current || !maskCanvasRef) return;
    const step = historyStackRef.current.redo();
    if (!step) return;
    const ctx = maskCanvasRef.getContext("2d");
    ctx.putImageData(step.snapshot, 0, 0);
    setHistory(historyStackRef.current.getList());
  }

  // Refs management
  function onAddRef(file) {
    setRefs([...refs, { file, name: file.name, url: URL.createObjectURL(file) }]);
  }
  function onRemoveRef(i) {
    const next = refs.slice();
    URL.revokeObjectURL(next[i].url);
    next.splice(i, 1);
    setRefs(next);
  }

  // Validation: whether the submit button is enabled.
  const canSubmit = useMemo(() => {
    if (status === "submitting" || status === "running") return false;
    if (!prompt.trim()) return false;
    if (outpaintMode) {
      return outpaint.directions.length > 0 && !!outpaint.amount;
    }
    if (!maskCanvasRef) return false;
    const painted = countMaskPaintedPixels(maskCanvasRef);
    if (painted < MIN_MASK_PIXELS) return false;
    return true;
  }, [status, prompt, outpaintMode, outpaint, maskCanvasRef, history]);

  const validationMessage = useMemo(() => {
    if (status === "submitting" || status === "running") return "";
    if (!maskCanvasRef || outpaintMode) return "";
    const painted = countMaskPaintedPixels(maskCanvasRef);
    if (painted < MIN_MASK_PIXELS) return "mask is empty — paint where you want changes";
    return "";
  }, [maskCanvasRef, outpaintMode, status, history]);

  async function onSubmit() {
    if (!canSubmit || !sourceJob) return;
    setStatus("submitting");
    setErrorState(null);
    setStatusHint("submitting…");
    try {
      const fullPrompt = negative.trim()
        ? `${prompt}\n\nAvoid: ${negative}`
        : prompt;
      const sizeMap = {
        sq: "1024x1024",
        land: "1536x1024",
        port: "1024x1536",
        auto: "auto",
      };
      // Build the payload incrementally — only include "advanced" fields
      // when the user actually changed them away from the default. The
      // backend rejects fields that the chosen model's capabilities don't
      // expose, even if the value is something innocuous like
      // ``thinking="off"`` (which semantically means "I didn't pick one").
      const payload = {
        model: sourceJob.model,
        prompt: fullPrompt,
        n: 1,
        parent_hash_id: sourceJob.hash_id,
        derivation_kind: outpaintMode ? "outpaint" : "mask_edit",
      };
      const mappedSize = sizeMap[advanced.size];
      if (mappedSize && mappedSize !== "auto") payload.size = mappedSize;
      if (advanced.quality && advanced.quality !== "auto") payload.quality = advanced.quality;
      // ``off`` is the "no thinking" sentinel the upstream uses internally;
      // sending it explicitly is treated as "user picked thinking" by the
      // validator, which fails when the provider's caps don't list it.
      if (advanced.thinking && advanced.thinking !== "off") payload.thinking = advanced.thinking;
      if (advanced.output) payload.output_format = advanced.output;
      if (advanced.streamPartial) payload.partial_images = 2;
      if (outpaintMode) {
        payload.outpaint_directions = outpaint.directions;
        payload.outpaint_amount = outpaint.amount;
      }

      // Build references: source image first, then user-added refs.
      const sourceBlob = await (await fetch(sourceImageUrl)).blob();
      const sourceFile = new File([sourceBlob], "source.png", { type: "image/png" });
      const refFiles = [sourceFile, ...refs.map((r) => r.file)];

      let maskBlob = null;
      if (!outpaintMode) {
        maskBlob = await exportMaskPng(maskCanvasRef);
      }

      const resp = await createJob({
        payload,
        references: refFiles,
        mask: maskBlob,
      });
      markMaskEditUsed();
      setStatus("running");
      setStatusHint("queued · waiting for upstream");

      // Poll for completion (real implementation should use SSE; we fall
      // back to polling so the page works without that wiring).
      const done = await pollUntilTerminal(resp.hash_id);
      if (done.status === "SUCCEEDED") {
        setStatus("done");
        setStatusHint(`completed · #${done.seq_no}`);
        setResultJob(done);
        const firstImg = (done.images || [])[0];
        if (firstImg) {
          const blob = await fetchImageBlob(imageOriginalUrl(done.hash_id, firstImg.order));
          if (blob) setResultUrl(URL.createObjectURL(blob));
        }
        // Fetch derived siblings of the parent.
        try {
          const derived = await getDerivedJobs(sourceJob.hash_id, { limit: 50 });
          setDerivedVersions(derived.items || []);
        } catch (e) {
          console.warn("derived list failed:", e);
        }
      } else {
        setStatus("error");
        setErrorState({ code: done.error || "GENERATION_FAILED", message: done.error || "Unknown error" });
      }
    } catch (e) {
      console.error("submit failed:", e);
      setStatus("error");
      setErrorState({
        code: e.code || "UPSTREAM_ERROR",
        message: e.message || "Submit failed",
      });
    }
  }

  async function pollUntilTerminal(targetHash, maxAttempts = 120, intervalMs = 1500) {
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const job = await getJob(targetHash);
        if (["SUCCEEDED", "FAILED", "CANCELLED"].includes(job.status)) {
          return job;
        }
      } catch (e) {
        // 404 may mean it hasn't propagated yet — keep polling briefly.
        if (i < 5) continue;
        throw e;
      }
      await new Promise((r) => setTimeout(r, intervalMs));
    }
    throw new Error("polling timeout");
  }

  // === Keyboard shortcuts ===============================================
  useEffect(() => {
    function onKey(e) {
      if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
      const key = e.key;
      const meta = e.metaKey || e.ctrlKey;
      if (key === "?") {
        e.preventDefault();
        setShowCheat((s) => !s);
      } else if (key === "Escape") {
        setShowCheat(false);
        if (errorState) setErrorState(null);
      } else if (meta && key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if (meta && (key === "Z" || (key.toLowerCase() === "z" && e.shiftKey))) {
        e.preventDefault();
        redo();
      } else if (meta && key === "Enter") {
        e.preventDefault();
        if (canSubmit) onSubmit();
      } else if (meta && key === "0") {
        e.preventDefault();
        canvasStageRef.current?.fit();
      } else if (meta && key === "1") {
        e.preventDefault();
        canvasStageRef.current?.actual();
      } else if (meta && (key === "=" || key === "+")) {
        e.preventDefault();
        canvasStageRef.current?.zoomBy(1.25);
      } else if (meta && key === "-") {
        e.preventDefault();
        canvasStageRef.current?.zoomBy(0.8);
      } else if (!meta) {
        const k = key.toLowerCase();
        if (k === "b") setTool("brush");
        else if (k === "e") setTool("eraser");
        else if (k === "m") setTool("rect");
        else if (k === "l") setTool("lasso");
        else if (k === "w") setTool("wand");
        else if (k === "v" || k === "h") setTool("pan");
        else if (k === "o") {
          setOutpaintMode((on) => !on);
          if (!outpaintMode) setTool("outpaint");
          else setTool("brush");
        } else if (key === "[") {
          setBrushOpts({ ...brushOpts, size: Math.max(1, brushOpts.size - 2) });
        } else if (key === "]") {
          setBrushOpts({ ...brushOpts, size: Math.min(200, brushOpts.size + 2) });
        } else if (key === "{") {
          setBrushOpts({ ...brushOpts, hardness: Math.max(0, brushOpts.hardness - 5) });
        } else if (key === "}") {
          setBrushOpts({ ...brushOpts, hardness: Math.min(100, brushOpts.hardness + 5) });
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [brushOpts, canSubmit, errorState, outpaintMode]);

  // === Render ===========================================================
  if (!sourceImage) {
    return (
      <div className="me-root" data-testid="me-loading">
        <div style={{ padding: 40, textAlign: "center", margin: "auto" }}>
          loading source image…
        </div>
      </div>
    );
  }

  // Compute outpaint bleed geometry for canvas overlay.
  const outpaintGeometry = (() => {
    if (!outpaintMode) return null;
    const a = parseAmount(outpaint.amount, imageW, imageH);
    return {
      left: outpaint.directions.includes("left") ? a.x : 0,
      right: outpaint.directions.includes("right") ? a.x : 0,
      top: outpaint.directions.includes("top") ? a.y : 0,
      bottom: outpaint.directions.includes("bottom") ? a.y : 0,
    };
  })();

  const showingCompare = status === "done" && resultJob;

  return (
    <div className="me-root" data-testid="me-root">
      <TopBar
        sourceLabel={sourceJob ? `#${sourceJob.seq_no}` : "—"}
        mode={outpaintMode ? "outpaint" : "inpaint"}
        model={sourceJob?.model}
        status={
          validationMessage ? "empty" :
          status
        }
        canSubmit={canSubmit}
        onBack={() => navigate("/archive")}
        onSubmit={onSubmit}
      />
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <Toolbar
          active={outpaintMode ? "outpaint" : tool}
          onPick={(id) => {
            if (id === "outpaint") {
              setOutpaintMode(true);
              setTool("outpaint");
            } else {
              setOutpaintMode(false);
              setTool(id);
              setTab("tool");
            }
          }}
          onUndo={undo}
          onRedo={redo}
          canUndo={historyStackRef.current?.canUndo()}
          canRedo={historyStackRef.current?.canRedo()}
        />
        {showingCompare ? (
          <CompareSurface
            beforeUrl={sourceImageUrl}
            afterUrl={resultUrl}
            parentLabel={`#${sourceJob.seq_no}`}
            derivedLabel={`#${resultJob.seq_no}`}
            derivationKind={outpaintMode ? "outpaint" : "mask edit"}
          />
        ) : (
          <CanvasStage
            ref={canvasStageRef}
            imageBitmap={sourceImage}
            imageW={imageW}
            imageH={imageH}
            tool={outpaintMode ? "pan" : tool}
            brushOpts={brushOpts}
            outpaintMode={outpaintMode}
            outpaintGeometry={outpaintGeometry}
            onCursorChange={(c) => setCursorXY({ x: c.imageX, y: c.imageY })}
            onMaskChange={onMaskChange}
            onReady={onCanvasReady}
            onBrushDelta={({ kind, delta }) => {
              setBrushOpts((b) => {
                if (kind === "size") {
                  const v = Math.max(1, Math.min(200, b.size + delta));
                  showHud(`brush ø ${Math.round(v)}px`);
                  return { ...b, size: v };
                }
                if (kind === "hardness") {
                  const v = Math.max(0, Math.min(100, b.hardness + delta));
                  showHud(`hardness ${Math.round(v)}%`);
                  return { ...b, hardness: v };
                }
                if (kind === "opacity") {
                  const v = Math.max(10, Math.min(100, b.opacity + delta));
                  showHud(`opacity ${Math.round(v)}%`);
                  return { ...b, opacity: v };
                }
                return b;
              });
            }}
            onZoomChange={setZoomDisplay}
            onHudMessage={showHud}
          />
        )}
        {showingCompare ? (
          <CompareRightPanel
            result={resultJob}
            parent={sourceJob}
            derivedVersions={derivedVersions}
            onVersionPick={(v) => navigate(`/edit/${v.hash_id}/1`)}
          />
        ) : (
          <RightPanel
            tab={tab}
            setTab={setTab}
            tool={tool}
            brushOpts={brushOpts}
            setBrushOpts={setBrushOpts}
            prompt={prompt}
            setPrompt={setPrompt}
            negative={negative}
            setNegative={setNegative}
            refs={refs}
            onAddRef={onAddRef}
            onRemoveRef={onRemoveRef}
            advanced={advanced}
            setAdvanced={setAdvanced}
            history={history}
            onJumpHistory={jumpHistory}
            onOp={applyOp}
            lastOp={lastOp}
            sourceThumbUrl={sourceImageUrl}
            outpaintMode={outpaintMode}
            outpaint={outpaint}
            setOutpaint={setOutpaint}
            imageW={imageW}
            imageH={imageH}
          />
        )}
      </div>
      <StatusBar
        zoom={zoomDisplay}
        dim={`${imageW}×${imageH}`}
        cursor={`(${cursorXY.x}, ${cursorXY.y})`}
        brush={Math.round(brushOpts.size)}
        hint={validationMessage || statusHint}
        onHelp={() => setShowCheat(true)}
      />
      {validationMessage && !showingCompare && (
        <div style={{ position: "absolute", left: 89, top: 92, zIndex: 12 }}>
          <ValidationBanner message={validationMessage} />
        </div>
      )}
      {streaming && <StreamingOverlay {...streaming} />}
      {errorState && (
        <ErrorModal
          code={errorState.code}
          message={errorState.message}
          onEditPrompt={() => { setErrorState(null); setTab("prompt"); }}
          onRetry={() => { setErrorState(null); onSubmit(); }}
          onCopy={() => navigator.clipboard?.writeText(errorState.code)}
          onClose={() => setErrorState(null)}
        />
      )}
      {hud && <HudToast text={hud} />}
      {showCheat && <CheatSheetOverlay onClose={() => setShowCheat(false)} />}
      {showingCompare && (
        <div className="me-compare-actions">
          <button
            className="btn shadowed primary"
            style={{ height: 42 }}
            onClick={() => navigate("/archive")}
            data-testid="me-compare-save-back"
          >
            save &amp; back to archive
          </button>
          <button
            className="btn shadowed"
            style={{ height: 42 }}
            onClick={() => {
              navigate(`/edit/${resultJob.hash_id}/1`);
              setStatus("idle");
              setResultJob(null);
              setResultUrl(null);
            }}
            data-testid="me-compare-continue"
          >
            continue editing this result
          </button>
          <a
            className="btn"
            style={{ height: 42, textDecoration: "none" }}
            href={resultUrl || "#"}
            download={`${resultJob?.hash_id || "result"}.png`}
            data-testid="me-compare-download"
          >
            download
          </a>
          <button
            className="btn"
            style={{ height: 42 }}
            onClick={() => {
              const firstImg = resultJob?.images?.[0];
              if (firstImg) {
                // optimistic — uses existing archive star endpoint
                window.dispatchEvent(new CustomEvent("me:pick", { detail: { hash_id: resultJob.hash_id, order: firstImg.order } }));
              }
            }}
            data-testid="me-compare-pick"
          >
            ★ pick
          </button>
          <button
            className="btn ghost"
            style={{ height: 42 }}
            onClick={() => alert("show mask: not yet wired in v1")}
            data-testid="me-compare-show-mask"
          >
            show mask
          </button>
        </div>
      )}
    </div>
  );
}

function parseAmount(amount, w, h) {
  if (!amount) return { x: 0, y: 0 };
  if (amount.endsWith("%")) {
    const p = parseInt(amount.slice(0, -1), 10) / 100;
    return { x: Math.round(w * p), y: Math.round(h * p) };
  }
  if (amount.endsWith("px")) {
    const p = parseInt(amount.slice(0, -2), 10);
    return { x: p, y: p };
  }
  return { x: 0, y: 0 };
}
