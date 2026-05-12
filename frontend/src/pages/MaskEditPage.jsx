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
import { useAuth } from "../store/auth.js";
import { useMaskDraftAutosave } from "../hooks/useMaskDraftAutosave.js";
import * as maskDraftDB from "../storage/maskDraftDB.js";
import * as archiveStore from "../store/archive.js";
import DraftToast from "../components/DraftToast.jsx";

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
  const { user } = useAuth();
  const userId = user?.id || null;

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

  // Holds a draft record while we wait for the mask canvas to mount,
  // so the restoration sequence works regardless of which arrives
  // first: the IDB read (driven by the hook) or the canvas onReady
  // event (driven by image load).
  const pendingRestoreRef = useRef(null);
  // Status-bar message for non-fatal restore notes ("mask sized
  // changed, restored text only" etc).
  const [restoreNote, setRestoreNote] = useState(null);

  function showHud(text) {
    setHud(text);
    if (hudTimerRef.current) clearTimeout(hudTimerRef.current);
    hudTimerRef.current = setTimeout(() => setHud(null), 1200);
  }

  useEffect(() => () => {
    if (hudTimerRef.current) clearTimeout(hudTimerRef.current);
  }, []);

  // Mount the archive store so the lineage panel has rows + SSE updates.
  // ``mount`` is idempotent — re-arming the same user is a no-op.
  useEffect(() => {
    if (!userId) return;
    void archiveStore.mount(userId);
  }, [userId]);

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
        // Source job is gone — drop any orphan draft so the user
        // doesn't see a phantom "resume" entry pointing to nothing.
        if (userId) {
          const orphanMode = searchParams.get("mode") === "outpaint" ? "outpaint" : "inpaint";
          const orphanId = maskDraftDB.makeDraftId(hashId, order, orphanMode);
          maskDraftDB.deleteDraft(userId, orphanId).catch(() => {});
        }
        alert(`Could not open editor: ${e.message || e}`);
        navigate("/archive");
      }
    })();
    return () => { cancelled = true; };
  }, [hashId, order, navigate, userId, searchParams]);

  // Apply a pending restore record to the mask canvas. Returns true
  // when the mask blob was painted, false otherwise (no blob,
  // dimension mismatch, or paint failed).
  async function applyMaskBlobToCanvas(canvas, record) {
    if (!canvas || !record || !record.mask_blob) return false;
    if (
      record.mask_w &&
      record.mask_h &&
      (record.mask_w !== canvas.width || record.mask_h !== canvas.height)
    ) {
      // Source size changed (job re-rendered, model swapped output).
      // Don't paint a wrong-sized mask; let the page surface a note.
      return false;
    }
    try {
      const bmp = await createImageBitmap(record.mask_blob);
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(bmp, 0, 0);
      return true;
    } catch {
      return false;
    }
  }

  // Initialise history stack once mask canvas is available, and
  // replay any pending mask restoration.
  const onCanvasReady = useCallback(({ sourceCanvas, maskCanvas }) => {
    setSourceCanvasRef(sourceCanvas);
    setMaskCanvasRef(maskCanvas);
    if (!historyStackRef.current && maskCanvas) {
      const pending = pendingRestoreRef.current;
      pendingRestoreRef.current = null;
      const finishInit = () => {
        const ctx = maskCanvas.getContext("2d");
        const snap = ctx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
        historyStackRef.current = createHistoryStack(snap, {
          kind: pending && pending.has_paint ? "restore" : "init",
          label: pending && pending.has_paint ? "restored draft" : "open source",
        });
        setHistory(historyStackRef.current.getList());
      };
      if (pending && pending.mask_blob) {
        applyMaskBlobToCanvas(maskCanvas, pending).then((painted) => {
          if (!painted && pending.has_paint) {
            setRestoreNote("mask size changed — restored prompt + settings only");
          }
          finishInit();
        });
      } else {
        finishInit();
      }
    }
  }, []);

  // ── Draft autosave ──────────────────────────────────────────────────
  const draftMode = outpaintMode ? "outpaint" : "inpaint";
  const onRestoreDraft = useCallback(
    (record) => {
      if (!record) return;
      // Source identity changed (job re-rendered to a different model)
      // — keep the text, drop the mask.
      const modelChanged =
        record.source_model && sourceJob?.model && record.source_model !== sourceJob.model;
      if (modelChanged) {
        setRestoreNote("source model changed — restored prompt + settings only");
      }
      if (typeof record.prompt === "string") setPrompt(record.prompt);
      if (typeof record.negative === "string") setNegative(record.negative);
      if (record.brush_opts && typeof record.brush_opts === "object") {
        setBrushOpts((b) => ({ ...b, ...record.brush_opts }));
      }
      if (record.advanced && typeof record.advanced === "object") {
        setAdvanced((a) => ({ ...a, ...record.advanced }));
      }
      if (record.outpaint && typeof record.outpaint === "object") {
        setOutpaint((o) => ({ ...o, ...record.outpaint }));
      }
      if (Array.isArray(record.refs) && record.refs.length > 0) {
        const restoredRefs = record.refs
          .filter((f) => f instanceof Blob)
          .map((file) => ({
            file,
            name: file.name || "ref",
            url: URL.createObjectURL(file),
          }));
        if (restoredRefs.length > 0) setRefs(restoredRefs);
      }
      if (record.active_tool) setTool(record.active_tool);
      if (record.active_tab) setTab(record.active_tab);
      // The canvas may not be mounted yet — stash the record so
      // onCanvasReady can paint the mask blob.
      if (!modelChanged && record.has_paint && record.mask_blob) {
        if (maskCanvasRef) {
          // Already mounted: paint immediately and reseed history.
          applyMaskBlobToCanvas(maskCanvasRef, record).then((painted) => {
            if (!painted) {
              setRestoreNote("mask size changed — restored prompt + settings only");
              return;
            }
            const ctx = maskCanvasRef.getContext("2d");
            const snap = ctx.getImageData(0, 0, maskCanvasRef.width, maskCanvasRef.height);
            historyStackRef.current = createHistoryStack(snap, {
              kind: "restore",
              label: "restored draft",
            });
            setHistory(historyStackRef.current.getList());
          });
        } else {
          pendingRestoreRef.current = record;
        }
      }
    },
    [maskCanvasRef, sourceJob]
  );

  const { toast: draftToast, clearDraft, markCanvasDirty } = useMaskDraftAutosave({
    userId,
    hashId,
    order,
    mode: draftMode,
    enabled: !!sourceImage && !!userId,
    prompt,
    negative,
    refs,
    brushOpts,
    advanced,
    outpaint,
    outpaintMode,
    activeTool: tool,
    activeTab: tab,
    maskCanvas: maskCanvasRef,
    sourceJob,
    status,
    imageW,
    imageH,
    onRestore: onRestoreDraft,
  });

  function captureSnapshot(meta) {
    if (!historyStackRef.current || !maskCanvasRef) return;
    const ctx = maskCanvasRef.getContext("2d");
    const snap = ctx.getImageData(0, 0, maskCanvasRef.width, maskCanvasRef.height);
    historyStackRef.current.push(snap, meta);
    setHistory(historyStackRef.current.getList());
    markCanvasDirty();
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
    markCanvasDirty();
  }
  function undo() {
    if (!historyStackRef.current || !maskCanvasRef) return;
    const step = historyStackRef.current.undo();
    if (!step) return;
    const ctx = maskCanvasRef.getContext("2d");
    ctx.putImageData(step.snapshot, 0, 0);
    setHistory(historyStackRef.current.getList());
    markCanvasDirty();
  }
  function redo() {
    if (!historyStackRef.current || !maskCanvasRef) return;
    const step = historyStackRef.current.redo();
    if (!step) return;
    const ctx = maskCanvasRef.getContext("2d");
    ctx.putImageData(step.snapshot, 0, 0);
    setHistory(historyStackRef.current.getList());
    markCanvasDirty();
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
      const orderNum = Math.max(1, parseInt(order, 10) || 1);
      const payload = {
        model: sourceJob.model,
        prompt: fullPrompt,
        n: 1,
        parent_hash_id: sourceJob.hash_id,
        parent_order: orderNum,
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
        // Submit succeeded → the work is on the server now, no need
        // to keep the local draft around.
        clearDraft({ silent: true }).catch(() => {});
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

  // Promote the freshly-rendered result job to be the new editing source.
  // Used by both "continue editing this result" and the auto-promotion
  // path when the user dismisses the compare view (PRD §5.7).
  //
  // Gated on a successful image decode: if we can't read the result
  // image, we keep the compare view intact rather than half-promote
  // (which would leave the URL pointing at a job whose pixels the
  // canvas hasn't loaded).
  async function promoteResultToSource() {
    if (!resultJob) return;
    const next = resultJob;
    const firstImg = (next.images || [])[0];
    if (!firstImg) return;

    let blob;
    let bmp;
    try {
      blob = await fetchImageBlob(imageOriginalUrl(next.hash_id, firstImg.order));
      if (!blob) throw new Error("result image is empty");
      bmp = await createImageBitmap(blob);
    } catch (e) {
      console.warn("promoteResultToSource: failed to decode result", e);
      // Keep compare view + result job alive so the user can retry
      // (e.g. via "continue editing this result").
      return;
    }

    // Source swap atomically — replace job + bitmap + URL together so
    // the canvas never renders against a stale pair.
    setSourceJob(next);
    setSourceImage(bmp);
    setImageW(bmp.width);
    setImageH(bmp.height);
    if (sourceImageUrl) URL.revokeObjectURL(sourceImageUrl);
    setSourceImageUrl(URL.createObjectURL(blob));

    // Reset mask history so subsequent ⌘Z doesn't jump to the previous
    // job's painted state.
    historyStackRef.current = null;
    setHistory([]);
    setStatus("idle");
    setStatusHint("paint a mask area before submitting (≥ 100 px)");
    setResultJob(null);
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    setResultUrl(null);
    setDerivedVersions([]);
    navigate(`/edit/${next.hash_id}/1`, { replace: true });
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
        else if (k === "v") setTool("pan");
        else if (k === "h") {
          // ``H`` opens the lineage / history tab. The original
          // "pan with H" shortcut moves to ``V`` only — the new
          // history view is more valuable as the first-class home.
          setTab("history");
        } else if (k === "o") {
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
        onBack={() => {
          // Only nag the user when there is something they could lose.
          const dirty =
            (maskCanvasRef && countMaskPaintedPixels(maskCanvasRef) > 0) ||
            !!prompt.trim() ||
            refs.length > 0;
          if (dirty && status !== "done") {
            const ok = window.confirm(
              "放弃这次未提交的 mask edit 吗？草稿会被清除，无法恢复。"
            );
            if (!ok) return;
          }
          clearDraft({ silent: true }).finally(() => navigate("/archive"));
        }}
        onSubmit={onSubmit}
        draftToast={draftToast}
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
            onOp={applyOp}
            lastOp={lastOp}
            sourceThumbUrl={sourceImageUrl}
            outpaintMode={outpaintMode}
            outpaint={outpaint}
            setOutpaint={setOutpaint}
            imageW={imageW}
            imageH={imageH}
            sourceHashId={sourceJob?.hash_id}
            sourceOrder={Math.max(1, parseInt(order, 10) || 1)}
          />
        )}
      </div>
      <StatusBar
        zoom={zoomDisplay}
        dim={`${imageW}×${imageH}`}
        cursor={`(${cursorXY.x}, ${cursorXY.y})`}
        brush={Math.round(brushOpts.size)}
        hint={restoreNote || validationMessage || statusHint}
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
            onClick={() => { void promoteResultToSource(); }}
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
