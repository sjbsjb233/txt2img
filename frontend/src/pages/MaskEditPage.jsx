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
import CheatSheetOverlay from "../components/maskeditor/CheatSheetOverlay.jsx";
import CompareSurface from "../components/maskeditor/CompareSurface.jsx";
import CompareRightPanel from "../components/maskeditor/CompareRightPanel.jsx";
import HudToast from "../components/maskeditor/HudToast.jsx";
import CanvasSkeleton from "../components/maskeditor/CanvasSkeleton.jsx";
import LockVeil from "../components/maskeditor/LockVeil.jsx";
import GeneratingCard from "../components/maskeditor/GeneratingCard.jsx";
import CanvasPartial from "../components/maskeditor/CanvasPartial.jsx";
import MEIcon from "../components/maskeditor/MEIcon.jsx";
import {
  recordMaskEditDuration,
  getEstimatedDuration,
} from "../config/maskEditTiming.js";

import { getJob, imageOriginalUrl, fetchImageBlob } from "../api/archive.js";
import { createJob, replaceJobImage } from "../api/jobs.js";
import { useCaptchaGate } from "../hooks/useCaptchaGate.jsx";
import { getDerivedJobs } from "../api/derived.js";
import { getModels } from "../api/models.js";
import { applyDefaults, reconcileParams, paramKey } from "../config/modelParams.js";
import { readSticky } from "../storage/stickyStore.js";
import {
  markMaskEditUsed,
  pickMaskMethod,
  resolveFallbackTemplate,
  FALLBACK_TEMPLATE_INPAINT,
  FALLBACK_TEMPLATE_OUTPAINT,
  pickPreservationStrategy,
} from "../config/maskEdit.js";
import { analyzeMaskEdit } from "../components/maskeditor/utils/diffAnalysis.js";
import { createHistoryStack } from "../components/maskeditor/history/HistoryStack.js";
import {
  applyAll, applyClear, applyInvert, applyFeather, applyExpand,
  applyContract, applySmooth,
} from "../components/maskeditor/ops/maskOps.js";
import {
  exportMaskPng,
  exportFallbackMaskPng,
  countMaskPaintedPixels,
  maskPaintedRatio,
} from "../components/maskeditor/utils/maskExport.js";
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

// Module-level set of `${hash}#${order}` keys that the page just
// promoted into and therefore wants the prompt to stay empty for.
// Module-scope (not React state / ref) so it survives the React 18
// StrictMode dev unmount/remount cycle that wipes refs and fires
// every effect twice. Once a key is in here, the load effect's
// ``setPrompt(job.prompt)`` skips that key forever — for this tab.
const PROMOTED_BLANK_KEYS = new Set();

// Map a schema-shaped ``params`` object back into the legacy
// ``advanced`` shape the right-rail Seg controls render. We keep the
// Seg UI as-is (it's still functional) but pre-fill it from whatever
// the source job actually used. Anything outside the four legacy keys
// is ignored — those land into ``params`` directly via the wire payload.
function paramsToAdvanced(params) {
  const advanced = { ...DEFAULT_ADVANCED };
  if (params && typeof params === "object") {
    if (typeof params.size === "string") {
      // The Seg labels are short ("sq" / "land" / "port" / "auto");
      // map back from the wire shape so a job that ran at 1024x1024
      // doesn't show "auto".
      if (params.size === "1024x1024") advanced.size = "sq";
      else if (params.size === "1536x1024") advanced.size = "land";
      else if (params.size === "1024x1536") advanced.size = "port";
      else if (params.size === "auto") advanced.size = "auto";
    }
    if (typeof params.quality === "string") {
      advanced.quality = params.quality;
    }
    if (typeof params.thinking === "string") {
      advanced.thinking = params.thinking;
    }
    if (typeof params.output_format === "string") {
      advanced.output = params.output_format;
    }
    if (typeof params.partial_images === "number" && params.partial_images > 0) {
      advanced.streamPartial = true;
    }
  }
  return advanced;
}

export default function MaskEditPage() {
  const { hashId, order } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const userId = user?.id || null;

  const [sourceJob, setSourceJob] = useState(null);
  const [sourceImage, setSourceImage] = useState(null); // ImageBitmap
  const [sourceImageUrl, setSourceImageUrl] = useState(null);
  // Surface load failures inline (in the canvas area) instead of as a
  // ``window.alert`` that yanks the user back to /archive without
  // explanation. ``null`` = still loading or already loaded.
  const [loadError, setLoadError] = useState(null);
  const [imageW, setImageW] = useState(1024);
  const [imageH, setImageH] = useState(1024);
  const [tool, setTool] = useState("brush");
  const [tab, setTab] = useState("tool");
  const [brushOpts, setBrushOpts] = useState(DEFAULT_BRUSH);
  const [prompt, setPrompt] = useState("");
  const [refs, setRefs] = useState([]);
  const [advanced, setAdvanced] = useState(DEFAULT_ADVANCED);
  const [history, setHistory] = useState([]);
  const [lastOp, setLastOp] = useState(null);
  const [status, setStatus] = useState("idle"); // idle/submitting/running/done/error/empty
  const [errorState, setErrorState] = useState(null); // {code, message}
  const [showCheat, setShowCheat] = useState(false);
  const [maskCanvasRef, setMaskCanvasRef] = useState(null);
  const [sourceCanvasRef, setSourceCanvasRef] = useState(null);
  const [statusHint, setStatusHint] = useState("describe a change · mask is optional");
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
  const [hud, setHud] = useState(null);
  // Wall-clock the submission started — feeds GeneratingCard's
  // elapsed/remaining display + the timing recorder. ``null`` while
  // the editor is idle.
  const [submitStartedAt, setSubmitStartedAt] = useState(null);
  // Partial-event log for the GeneratingCard. Today the backend
  // doesn't emit them; the array stays empty and the card falls back
  // to its smooth fake-progress curve. Exists so wiring partials in
  // later is a one-line setPartials call away.
  const [partials] = useState([]);

  // Captcha state machine — same hook used by Create / Batch so the
  // user sees a consistent Turnstile flow no matter which page they
  // hit the burst threshold on.
  const captchaGate = useCaptchaGate({ modelId: sourceJob?.model });

  // Mask method is decided once, on load, from the model's capabilities
  // surfaced via /api/models. ``null`` until we know — we render a
  // loading state in the meantime so we don't accidentally route a
  // fallback model down the native path (or vice versa).
  const [maskMethod, setMaskMethod] = useState(null); // "native" | "fallback" | null
  // Active model for the editor session (resolved via the chain in the
  // load effect below). Locked for the entire session — switching
  // native ↔ fallback mid-session would change the multipart shape and
  // surprise the user. ``null`` while we wait for /api/models.
  const [selectedModel, setSelectedModel] = useState(null);
  // The full params object that ships on the wire. Defaults inherit
  // from the source job; reconciled against the active model's
  // ui_schema so nothing illegal sneaks through.
  const [params, setParams] = useState({});
  // User-facing notice when we had to fall back to a different model
  // because the source job's model is no longer mask-edit-capable.
  const [modelNotice, setModelNotice] = useState(null);

  // Fallback template state — only meaningful when maskMethod === "fallback".
  // We render the base template by default but let pro users unlock + edit.
  // ``customTemplate`` is wired into useMaskDraftAutosave below so a tab
  // close / reload restores the user's edits. The lock + open toggles are
  // intentionally ephemeral — every fresh session opens locked + collapsed
  // so the user doesn't trip over their own past edits without realising.
  // Resolved prompt at submit time is the only thing the backend ever sees.
  const [templateOpen, setTemplateOpen] = useState(false);
  const [templateLocked, setTemplateLocked] = useState(true);
  const [customTemplate, setCustomTemplate] = useState(null); // null = use default

  // Compare-mode state.
  const [strategy, setStrategy] = useState(null); // "mask" | "full"
  const [autoStrategy, setAutoStrategy] = useState(null); // baseline before override
  const [diffResult, setDiffResult] = useState(null); // {metrics, heatmapUrl, tier}
  const [analyzing, setAnalyzing] = useState(false);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [accepting, setAccepting] = useState(false);

  const historyStackRef = useRef(null);
  const canvasStageRef = useRef(null);
  const hudTimerRef = useRef(null);
  // Snapshot of "what counts as not edited" — captured after restore (or
  // initial mount when there's no draft). Anything matching the baseline
  // is treated as untouched: the autosave hook suppresses both writes
  // and the DRAFT SAVED toast. Without this, clicking the prompt
  // textarea on a job whose source prompt is non-empty (the default!)
  // would trip a meaningless save on the very first debounce tick.
  const [baseline, setBaseline] = useState(null);

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

  // Tracks the last URL pair we actually loaded. The path-change
  // effect compares against this so a soft history switch (which
  // ``replace``-navigates the URL after the swap) doesn't trip a
  // second load that wipes the just-painted state.
  const loadedKeyRef = useRef(null);
  // Set during a soft switch so the canvas area shows a skeleton
  // overlay while we fetch the new image, but the rest of the chrome
  // stays mounted (history panel still on screen, can keep clicking).
  const [softSwitching, setSoftSwitching] = useState(false);

  const loadSource = useCallback(
    async (targetHash, targetOrder, opts = {}) => {
      const { keepTab = true, soft = false } = opts;
      if (!targetHash) return;
      try {
        if (soft) setSoftSwitching(true);
        const job = await getJob(targetHash);
        let modelsResp = null;
        try {
          modelsResp = await getModels();
        } catch (e) {
          console.warn("getModels failed", e);
        }
        const allModels = modelsResp?.models || [];

        // ---- Model resolution chain (Feature 7) -----------------
        // ``available === false`` means admin disabled the model — we
        // treat that as "can't mask" so the fallback chain triggers.
        function pickMethodFor(model) {
          if (!model) return "unsupported";
          if (model.available === false) return "unsupported";
          return pickMaskMethod(model.capabilities);
        }
        let chosenModel = allModels.find((m) => m.model_id === job.model);
        let chosenMethod = pickMethodFor(chosenModel);
        let notice = null;
        if (!chosenModel || chosenMethod === "unsupported") {
          const sticky = readSticky(userId);
          const stickyId = sticky?.last_model_id || null;
          const fromSticky = stickyId
            ? allModels.find((m) => m.model_id === stickyId)
            : null;
          if (fromSticky && pickMethodFor(fromSticky) !== "unsupported") {
            chosenModel = fromSticky;
            chosenMethod = pickMethodFor(fromSticky);
            notice = `source model unavailable · using ${fromSticky.model_id}`;
          } else {
            const firstOk = allModels.find(
              (m) => pickMethodFor(m) !== "unsupported"
            );
            if (firstOk) {
              chosenModel = firstOk;
              chosenMethod = pickMethodFor(firstOk);
              notice = `source model unavailable · using ${firstOk.model_id}`;
            } else {
              setLoadError("No available model supports mask editing.");
              setSoftSwitching(false);
              return;
            }
          }
        }
        setMaskMethod(chosenMethod);
        setSelectedModel(chosenModel);
        setModelNotice(notice);
        // ---- Param inheritance chain (Feature 7) ----------------
        let baseParams;
        if (chosenModel.model_id === job.model && job.params) {
          baseParams = { ...(job.params || {}) };
        } else {
          const sticky = readSticky(userId);
          const stickyParams =
            sticky?.params_by_model?.[chosenModel.model_id];
          baseParams = stickyParams ? { ...stickyParams } : {};
        }
        const reconciled = applyDefaults(
          chosenModel.defaults,
          baseParams,
          chosenModel.capabilities,
          chosenModel.ui_schema
        );
        setParams(reconciled);
        setAdvanced(paramsToAdvanced(reconciled));

        setSourceJob(job);
        const requestedOrder = parseInt(targetOrder, 10) || 1;
        // Set the job's prompt as the default UNLESS this exact
        // (hash, order) was just freshly promoted from a result —
        // in that case the page deliberately cleared the prompt and
        // we don't want to refill it. The marker is module-scoped so
        // it survives React 18 StrictMode dev re-mounts, and never
        // cleared so multiple repeated loadSource calls all honor it.
        const thisKey = `${job.hash_id}#${requestedOrder}`;
        if (PROMOTED_BLANK_KEYS.has(thisKey)) {
          // Honor the cleared prompt; do NOT setPrompt here.
        } else if (job.prompt) {
          setPrompt(job.prompt);
        }
        const img = (job.images || []).find((i) => i.order === requestedOrder);
        if (!img) {
          setLoadError("Image not found for this order.");
          setSoftSwitching(false);
          return;
        }
        const url = imageOriginalUrl(job.hash_id, img.order);
        const blob = await fetchImageBlob(url);
        if (!blob) {
          setLoadError("Source image is no longer available.");
          setSoftSwitching(false);
          return;
        }
        const bmp = await createImageBitmap(blob);
        // Atomic source replacement: revoke the old URL after the new
        // one is in hand so the canvas never renders against a half-
        // swapped pair.
        if (sourceImageUrl) URL.revokeObjectURL(sourceImageUrl);
        setImageW(bmp.width);
        setImageH(bmp.height);
        setSourceImage(bmp);
        setSourceImageUrl(URL.createObjectURL(blob));
        setLoadError(null);
        // Soft-switch reset: drop mask history (would jump to wrong
        // image's painted state on ⌘Z) + result/diff (a result of the
        // previous source is no longer relevant).
        if (soft) {
          historyStackRef.current = null;
          setHistory([]);
          setResultJob(null);
          if (resultUrl) URL.revokeObjectURL(resultUrl);
          setResultUrl(null);
          if (diffResult?.heatmapUrl) URL.revokeObjectURL(diffResult.heatmapUrl);
          setDiffResult(null);
          setStrategy(null);
          setAutoStrategy(null);
          setStatus("idle");
          setBaseline(null);
          if (!keepTab) setTab("tool");
        }
        loadedKeyRef.current = `${job.hash_id}#${requestedOrder}`;
      } catch (e) {
        console.error("Failed to load source:", e);
        if (userId) {
          const orphanMode = searchParams.get("mode") === "outpaint" ? "outpaint" : "inpaint";
          const orphanId = maskDraftDB.makeDraftId(targetHash, targetOrder, orphanMode);
          maskDraftDB.deleteDraft(userId, orphanId).catch(() => {});
        }
        setLoadError(e.message || String(e) || "Could not open editor.");
      } finally {
        setSoftSwitching(false);
      }
    },
    // sourceImageUrl / resultUrl / diffResult intentionally excluded —
    // we only need their *latest* values at call time, not as deps
    // that re-create this callback every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [userId, searchParams]
  );

  // Initial mount load + URL-change reload guard. The guard rejects
  // routing changes that come from our own ``navigate(..., { replace })``
  // after a soft switch — the load already happened in ``loadSource``.
  useEffect(() => {
    const key = `${hashId}#${order}`;
    if (loadedKeyRef.current === key) return;
    // Skip when the page already has the matching source in state
    // (covers post-promote / soft-switch where loadSource set things
    // up directly and React 18 StrictMode wiped the ref on a dev
    // re-mount).
    if (sourceJob?.hash_id === hashId) {
      loadedKeyRef.current = key;
      return;
    }
    loadSource(hashId, order, { soft: false });
  }, [hashId, order, loadSource, sourceJob]);

  // Soft "switch source" handler used by the lineage panel. Loads the
  // new source in-place (canvas area shows a skeleton) without
  // remounting the page; URL updates via ``replace`` *after* state
  // settles so the URL-effect's guard skips the reload.
  const onPickNode = useCallback(
    async (nextHash, nextOrder) => {
      if (!nextHash) return;
      const targetOrder = nextOrder || 1;
      if (nextHash === hashId && String(targetOrder) === String(order)) return;
      await loadSource(nextHash, targetOrder, { soft: true, keepTab: true });
      // URL sync — replace, not push, so back button doesn't fill up
      // with breadcrumb noise. The load already happened, so the
      // mount effect will detect ``loadedKeyRef === key`` and skip.
      navigate(`/edit/${nextHash}/${targetOrder}`, { replace: true });
    },
    [hashId, order, loadSource, navigate]
  );

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
      // Fallback template (v2+ schema). Older records omit the field;
      // a string means the user customized it, null means use default.
      if (typeof record.custom_template === "string") {
        setCustomTemplate(record.custom_template);
      }
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

  const { toast: draftToast, clearDraft, markCanvasDirty, flushNow } = useMaskDraftAutosave({
    userId,
    hashId,
    order,
    mode: draftMode,
    enabled: !!sourceImage && !!userId,
    prompt,
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
    customTemplate,
    baseline,
    // Pass the history length so painting / undo / redo / op trips
    // the autosave debounce. Without this, mask edits don't fire a
    // save until the user changes some other state.
    canvasRev: history.length,
    onRestore: onRestoreDraft,
  });

  // Capture the baseline once the page has finished its initial setup
  // (image loaded, draft restore — if any — has run). The autosave hook
  // diffs against this snapshot to decide whether to flash DRAFT SAVED.
  // We wait one tick after sourceImage arrives so any pendingRestore
  // had a chance to call onRestoreDraft → setPrompt etc.
  useEffect(() => {
    if (!sourceImage || baseline) return;
    const t = setTimeout(() => {
      setBaseline({
        prompt: prompt || "",
        refsLen: refs.length,
        canvasRev: 0,
        advanced: { ...(advanced || {}) },
        outpaint: { ...(outpaint || {}) },
        outpaintMode,
        customTemplate: customTemplate ?? null,
      });
    }, 250);
    return () => clearTimeout(t);
    // We *want* a stable baseline — only re-take it if the source
    // image is replaced (e.g. after a soft-switch via history panel).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceImage, baseline]);

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

  // While a job is in flight (submitting → running → finalizing), we
  // lock the page entirely so the user can't drift the prompt / mask /
  // params out from under the request that already left the browser.
  const locked =
    status === "submitting" ||
    status === "running" ||
    status === "finalizing";

  // Has the user actually painted enough mask to count? Drives the
  // submit-path branching below: with-mask vs prompt-only.
  const maskPainted = useMemo(() => {
    if (!maskCanvasRef) return false;
    return countMaskPaintedPixels(maskCanvasRef) >= MIN_MASK_PIXELS;
  }, [maskCanvasRef, history]);

  // Validation: whether the submit button is enabled. Inpaint mode no
  // longer requires a mask — a non-empty prompt is enough; mask just
  // narrows the edit region.
  const canSubmit = useMemo(() => {
    if (locked) return false;
    // No submitting before the source image is in hand: we'd fail at
    // multipart build time when the source blob isn't ready.
    if (!sourceImage || !sourceImageUrl) return false;
    if (!prompt.trim()) return false;
    if (outpaintMode) {
      return outpaint.directions.length > 0 && !!outpaint.amount;
    }
    return true;
  }, [locked, sourceImage, sourceImageUrl, prompt, outpaintMode, outpaint]);

  // Hard validation messages (red banner). Inpaint with no mask is
  // *not* an error any more — only outpaint-without-direction trips
  // this. See ``modeHint`` below for the soft "no mask" notice.
  const validationMessage = useMemo(() => {
    if (locked) return "";
    if (outpaintMode) {
      if (!outpaint.directions.length || !outpaint.amount) {
        return "pick at least one outpaint direction";
      }
    }
    return "";
  }, [locked, outpaintMode, outpaint]);

  // Soft "you're about to submit without a mask" hint — informational,
  // not an error. Surfaced in the StatusBar hint slot.
  const modeHint = useMemo(() => {
    if (locked || outpaintMode) return "";
    if (!prompt.trim() && !maskPainted) {
      return "describe the change you want — optionally paint a mask to limit it";
    }
    if (prompt.trim() && !maskPainted) {
      return "no mask · prompt will edit the whole image";
    }
    return "";
  }, [locked, outpaintMode, prompt, maskPainted]);

  async function onSubmit() {
    if (!canSubmit || !sourceJob) return;
    setStatus("submitting");
    setErrorState(null);
    setStatusHint("submitting…");
    setSubmitStartedAt(Date.now());
    try {
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
      // For the fallback path we prepend a system-prompt template so
      // the model knows what the second reference (the bw mask) means.
      // The resolved prompt is what hits the wire — backend has no
      // awareness of the template, by design.
      let finalPrompt = prompt;
      // Fallback template ONLY makes sense when there's a real mask to
      // describe. When the user submits prompt-only (no painted mask)
      // we ship the bare prompt: wrapping it in "the second reference
      // is a black-and-white mask" template would confuse the model.
      if (maskMethod === "fallback" && !outpaintMode && maskPainted) {
        const tpl = customTemplate ?? FALLBACK_TEMPLATE_INPAINT;
        finalPrompt = resolveFallbackTemplate(tpl, prompt);
      } else if (maskMethod === "fallback" && outpaintMode) {
        const tpl = customTemplate ?? FALLBACK_TEMPLATE_OUTPAINT;
        finalPrompt = resolveFallbackTemplate(tpl, prompt);
      }
      const payload = {
        model: selectedModel?.model_id || sourceJob.model,
        prompt: finalPrompt,
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
      // Schema-inherited params (Feature 7): anything in the source
      // job that the active model still accepts ships unless the
      // advanced UI already covered it. The advanced UI keys win when
      // both define the same field — that's how user edits to the Seg
      // controls override an inherited default.
      if (selectedModel && params && typeof params === "object") {
        const reconciled = reconcileParams(
          params,
          selectedModel.capabilities,
          selectedModel.ui_schema
        );
        for (const [k, v] of Object.entries(reconciled)) {
          if (k === "model" || k === "prompt" || k === "n") continue;
          // Don't shadow an explicit advanced choice the user just made.
          if (k === "size" && payload.size != null) continue;
          if (k === "quality" && payload.quality != null) continue;
          if (k === "thinking" && payload.thinking != null) continue;
          if (k === "output_format" && payload.output_format != null) continue;
          if (k === "partial_images" && payload.partial_images != null) continue;
          if (v === undefined || v === null || v === "") continue;
          payload[k] = v;
        }
      }
      if (outpaintMode) {
        payload.outpaint_directions = outpaint.directions;
        payload.outpaint_amount = outpaint.amount;
      }

      // Build references + mask depending on routing.
      //   native   → references = [source, ...userRefs]; mask = alpha PNG
      //   fallback → references = [source, bw_mask, ...userRefs]; mask = null
      // ⚠ For fallback the bw mask MUST come right after source; otherwise
      // the model may grab a user-supplied ref as the masking signal.
      // Outpaint is always native-shaped (backend synthesizes a mask).
      const sourceBlob = await (await fetch(sourceImageUrl)).blob();
      const sourceFile = new File([sourceBlob], "source.png", { type: "image/png" });

      let refFiles;
      let maskBlob = null;
      if (outpaintMode) {
        refFiles = [sourceFile, ...refs.map((r) => r.file)];
      } else if (!maskPainted) {
        // Prompt-only path (Feature 9): no mask, no fallback template.
        // Just the source + user refs, prompt as-is. The model edits
        // the whole image based on the prompt.
        refFiles = [sourceFile, ...refs.map((r) => r.file)];
        maskBlob = null;
      } else if (maskMethod === "fallback") {
        const bwMask = await exportFallbackMaskPng(maskCanvasRef);
        const bwMaskFile = new File([bwMask], "fallback_mask.png", { type: "image/png" });
        refFiles = [sourceFile, bwMaskFile, ...refs.map((r) => r.file)];
        maskBlob = null;
      } else {
        refFiles = [sourceFile, ...refs.map((r) => r.file)];
        maskBlob = await exportMaskPng(maskCanvasRef);
      }

      // Send via the captcha gate so we get the same UX as Create /
      // Batch when the burst threshold trips. ``precheckThenRun``
      // returns whatever the inner function returns; we surface that
      // out into ``resp`` for the rest of the submission flow.
      const submitOnce = async (token) => {
        const p = token ? { ...payload, captcha_token: token } : payload;
        return await createJob({
          payload: p,
          references: refFiles,
          mask: maskBlob,
        });
      };
      let resp;
      try {
        resp = await captchaGate.precheckThenRun(submitOnce);
      } catch (err) {
        if (err?.code === "CAPTCHA_REQUIRED") {
          // Precheck said "no captcha" but the backend's gate
          // disagreed (rolling counter ticked between precheck and
          // POST). Open the modal mid-flight and retry once.
          const token = await captchaGate.acquireMidFlight();
          if (!token) throw err;
          resp = await submitOnce(token);
        } else {
          throw err;
        }
      }
      markMaskEditUsed();
      setStatus("running");
      setStatusHint("queued · waiting for upstream");

      // Poll for completion (real implementation should use SSE; we fall
      // back to polling so the page works without that wiring).
      const done = await pollUntilTerminal(resp.hash_id);
      if (done.status === "SUCCEEDED") {
        // Brief "finalizing" phase: the GeneratingCard's progress bar
        // animates from its asymptotic ~92% up to 100% during this
        // window while we fetch + decode the result image. Without
        // this, the card would either pop closed before the compare
        // view paints (jarring) or look stuck at 92% (fake-y).
        // The GeneratingCard's progress bar climbs from its asymptotic
        // ~92% up toward 100% during the finalizing window while we
        // fetch + decode the result image. That keeps the user looking
        // at "almost done" feedback through the otherwise blank
        // network round-trip; the card disappears only after we
        // transition to ``done`` further below.
        setStatus("finalizing");
        setResultJob(done);
        // Record the actual render duration so future estimates get
        // smarter. Prefer the backend's stamp; fall back to the
        // wall-clock we kept.
        const trueRenderSec =
          typeof done.timing?.render_seconds === "number"
            ? done.timing.render_seconds
            : submitStartedAt
            ? (Date.now() - submitStartedAt) / 1000
            : null;
        if (trueRenderSec != null) {
          recordMaskEditDuration(
            selectedModel?.model_id || sourceJob.model,
            trueRenderSec
          );
        }
        // Submit succeeded → the work is on the server now, no need
        // to keep the local draft around.
        clearDraft({ silent: true }).catch(() => {});
        const firstImg = (done.images || [])[0];
        if (firstImg) {
          const blob = await fetchImageBlob(imageOriginalUrl(done.hash_id, firstImg.order));
          if (blob) {
            setResultUrl(URL.createObjectURL(blob));
            // Kick off diff analysis. Use the cached source bitmap
            // and the freshly-decoded result bitmap; mask canvas is
            // still in hand for the painted region. Skip when the
            // user submitted prompt-only — no mask = no meaningful
            // "preserve region" to compute, just lock strategy=full.
            if (!outpaintMode && maskCanvasRef && sourceImage && maskPainted) {
              let resBmp = null;
              try {
                setAnalyzing(true);
                resBmp = await createImageBitmap(blob);
                const ar = await analyzeMaskEdit({
                  source: sourceImage,
                  result: resBmp,
                  mask: maskCanvasRef,
                });
                const heatUrl = URL.createObjectURL(ar.heatmapBlob);
                const picked = pickPreservationStrategy(ar.metrics.preserve_change_pct);
                setDiffResult({
                  metrics: ar.metrics,
                  tier: ar.tier,
                  heatmapUrl: heatUrl,
                });
                setAutoStrategy(picked);
                setStrategy(picked);
              } catch (e) {
                console.warn("diff analysis failed:", e);
              } finally {
                // Release GPU memory for the decoded result bitmap.
                // Without close(), repeated edit/regen sessions stack up.
                resBmp?.close?.();
                setAnalyzing(false);
              }
            } else if (!outpaintMode && !maskPainted) {
              // Prompt-only path: no mask to preserve, so the only
              // sensible accept strategy is "full replace" of source.
              setAutoStrategy("full");
              setStrategy("full");
            }
          }
        }
        // Fetch derived siblings of the parent.
        try {
          const derived = await getDerivedJobs(sourceJob.hash_id, { limit: 50 });
          setDerivedVersions(derived.items || []);
        } catch (e) {
          console.warn("derived list failed:", e);
        }
        // Final transition out of the locked window. Doing this here
        // (after image fetch + diff analysis settle) means the
        // GeneratingCard stays up through the "almost there" tail
        // instead of unmounting on the SUCCEEDED-ack and leaving the
        // user staring at a blank locked editor.
        setStatus("done");
        setStatusHint(`completed · #${done.seq_no}`);
        setSubmitStartedAt(null);
      } else {
        setStatus("error");
        setSubmitStartedAt(null);
        setErrorState({ code: done.error || "GENERATION_FAILED", message: done.error || "Unknown error" });
      }
    } catch (e) {
      console.error("submit failed:", e);
      setStatus("error");
      setSubmitStartedAt(null);
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
    setStatusHint("describe a change · mask is optional");
    setResultJob(null);
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    setResultUrl(null);
    setDerivedVersions([]);
    // Feature 8: clear the user prompt after promote so the next round
    // starts blank. The previous prompt described the change we just
    // executed — keeping it would invite a stale resubmission. The
    // ``customTemplate`` (system fallback wrap) is intentionally left
    // alone; it's a separate state owned by the fallback path.
    setPrompt("");
    // Module-scoped marker (see top of file) — gates the load
    // effect's prompt refill so the cleared state sticks even
    // through StrictMode re-mounts and multiple loadSource calls.
    PROMOTED_BLANK_KEYS.add(`${next.hash_id}#1`);
    showHud("ready for next edit");
    // Reset the dirty baseline so the now-empty prompt doesn't look
    // "edited away" relative to the previous baseline.
    setBaseline(null);
    // Mark the new URL as already-loaded so the URL-change effect
    // doesn't trip a fresh load and overwrite the now-empty prompt
    // with the result job's prompt (which is the same as the source's
    // for a typical mask edit).
    loadedKeyRef.current = `${next.hash_id}#1`;
    navigate(`/edit/${next.hash_id}/1`, { replace: true });
  }

  // Compose the source + result through the painted mask, producing
  // a single PNG: edit-region pixels come from the result, preserve-
  // region pixels come from the source. Used by accept (mask-only).
  async function composeMaskOnlyOverlay() {
    if (!sourceImage || !resultUrl || !maskCanvasRef) return null;
    const resBlob = await (await fetch(resultUrl)).blob();
    const resBmp = await createImageBitmap(resBlob);
    try {
      return await composeMaskOnlyOverlayInner(resBmp);
    } finally {
      // Free the decoded bitmap. Long edit sessions accept/regenerate
      // many times; without close() each accumulates ImageBitmap GPU memory.
      resBmp.close?.();
    }
  }

  async function composeMaskOnlyOverlayInner(resBmp) {
    const w = sourceImage.width;
    const h = sourceImage.height;
    const off = document.createElement("canvas");
    off.width = w;
    off.height = h;
    const ctx = off.getContext("2d");
    // Start with the source.
    ctx.drawImage(sourceImage, 0, 0, w, h);
    // Build an alpha-only mask matching the painted edit region (where
    // the user wants the result). Use a temp canvas: paint a fully
    // opaque white wherever mask alpha > threshold.
    const tmp = document.createElement("canvas");
    tmp.width = w;
    tmp.height = h;
    const tctx = tmp.getContext("2d");
    const srcMask = maskCanvasRef.getContext("2d").getImageData(0, 0, maskCanvasRef.width, maskCanvasRef.height);
    const outMask = tctx.createImageData(maskCanvasRef.width, maskCanvasRef.height);
    for (let i = 0; i < srcMask.data.length; i += 4) {
      const on = srcMask.data[i + 3] > 16 ? 255 : 0;
      outMask.data[i] = 255;
      outMask.data[i + 1] = 255;
      outMask.data[i + 2] = 255;
      outMask.data[i + 3] = on;
    }
    tctx.putImageData(outMask, 0, 0);
    // Place the result over the source clipped to the mask:
    //   result_visible_in_mask = result × mask_alpha
    //   final = source where mask=0; result where mask=1
    const resCanvas = document.createElement("canvas");
    resCanvas.width = w;
    resCanvas.height = h;
    const rctx = resCanvas.getContext("2d");
    rctx.drawImage(resBmp, 0, 0, w, h);
    rctx.globalCompositeOperation = "destination-in";
    rctx.drawImage(tmp, 0, 0, w, h);
    rctx.globalCompositeOperation = "source-over";
    // Paint over the source.
    ctx.drawImage(resCanvas, 0, 0);
    return await new Promise((resolve, reject) => {
      off.toBlob((b) => (b ? resolve(b) : reject(new Error("composite toBlob failed"))), "image/png");
    });
  }

  async function onAcceptChanges() {
    if (!resultJob || accepting) return;
    setAccepting(true);
    try {
      if (strategy === "mask" && !outpaintMode) {
        try {
          const blob = await composeMaskOnlyOverlay();
          if (blob) {
            const file = new File([blob], "composite.png", { type: "image/png" });
            await replaceJobImage(resultJob.hash_id, file, "mask_only_overlay");
            showHud("composited · saved");
          }
        } catch (e) {
          // 409 means it was already composited — that's fine, fall
          // through to promote so we don't get stuck.
          if (e?.status !== 409) {
            console.warn("replace_image failed:", e);
            alert(`Failed to save composite: ${e.message || e}`);
            setAccepting(false);
            return;
          }
        }
      }
      // Cleanup compare state, promote result, stay in editor.
      if (diffResult?.heatmapUrl) URL.revokeObjectURL(diffResult.heatmapUrl);
      setDiffResult(null);
      setStrategy(null);
      setAutoStrategy(null);
      setShowHeatmap(false);
      await promoteResultToSource();
    } finally {
      setAccepting(false);
    }
  }

  function onDiscardChanges() {
    // No confirm, no API. The result job remains in archive / lineage /
    // versions; this is purely "don't continue editing on top of it".
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    if (diffResult?.heatmapUrl) URL.revokeObjectURL(diffResult.heatmapUrl);
    setResultJob(null);
    setResultUrl(null);
    setDiffResult(null);
    setStrategy(null);
    setAutoStrategy(null);
    setShowHeatmap(false);
    setStatus("idle");
    setStatusHint("describe a change · mask is optional");
    showHud("discarded · result kept in versions");
  }

  function onRegenerateSameParams() {
    // The current compare result stays in derivedVersions; we just
    // submit a brand-new job with the same prompt/mask/refs.
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    if (diffResult?.heatmapUrl) URL.revokeObjectURL(diffResult.heatmapUrl);
    setResultJob(null);
    setResultUrl(null);
    setDiffResult(null);
    setStrategy(null);
    setAutoStrategy(null);
    setShowHeatmap(false);
    setStatus("idle");
    // onSubmit reads the live state, which is unchanged — so this
    // produces a sibling job derived from the same source.
    setTimeout(() => { void onSubmit(); }, 0);
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
      // While locked, only ``?`` (cheat sheet) and ``Escape`` (back to
      // archive) get through. Everything else would either no-op or
      // produce phantom mask edits the user can't see.
      if (locked) {
        if (key === "?") {
          e.preventDefault();
          setShowCheat((s) => !s);
        } else if (key === "Escape") {
          clearDraft({ silent: true }).finally(() => navigate("/archive"));
        }
        return;
      }
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
  }, [brushOpts, canSubmit, errorState, outpaintMode, locked, navigate, clearDraft]);

  // === Render ===========================================================
  // Note: we intentionally do NOT early-return on ``!sourceImage``.
  // The page chrome (top bar / toolbar / right panel / status) renders
  // immediately so the user can see where they are and what's coming;
  // the canvas slot shows a skeleton (or error card) until the bitmap
  // lands. This avoids the "page swaps to a centered loading line and
  // back" flicker when entering the editor or soft-switching history.
  const imageReady = !!sourceImage && !loadError;

  // Compute outpaint bleed geometry for canvas overlay.
  const outpaintGeometry = (() => {
    if (!outpaintMode || !imageReady) return null;
    const a = parseAmount(outpaint.amount, imageW, imageH);
    return {
      left: outpaint.directions.includes("left") ? a.x : 0,
      right: outpaint.directions.includes("right") ? a.x : 0,
      top: outpaint.directions.includes("top") ? a.y : 0,
      bottom: outpaint.directions.includes("bottom") ? a.y : 0,
    };
  })();

  const showingCompare = status === "done" && resultJob;
  // Disable the right-panel + tools while the source bitmap is in
  // flight or has errored. Soft (canvas-only) — page chrome stays
  // visible. Soft-switching counts too: even though we still have a
  // (stale) sourceImage, we want the canvas slot to show a skeleton
  // while the new image fetches.
  const canvasUnavailable = !imageReady || softSwitching;

  return (
    <div className="me-root" data-testid="me-root">
      <TopBar
        sourceLabel={sourceJob ? `#${sourceJob.seq_no}` : "—"}
        mode={outpaintMode ? "outpaint" : "inpaint"}
        model={selectedModel?.model_id || sourceJob?.model}
        status={
          validationMessage ? "empty" :
          status
        }
        canSubmit={canSubmit}
        onBack={() => {
          // Leaving = silently flush the draft, no confirm dialog. The
          // user can always come back to the same image and pick up
          // where they left off (autosave restore on mount). When the
          // editor is locked (job in flight) the local draft is moot —
          // the work is server-side now — so just clear and leave.
          if (locked) {
            clearDraft({ silent: true }).finally(() => navigate("/archive"));
            return;
          }
          flushNow().finally(() => navigate("/archive"));
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
            heatmapUrl={diffResult?.heatmapUrl || null}
            showHeatmap={showHeatmap}
          />
        ) : canvasUnavailable ? (
          <div className="me-stage" data-testid="me-stage-skeleton" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <CanvasSkeleton
              error={loadError}
              onBack={() => navigate("/archive")}
            />
          </div>
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
            metrics={diffResult?.metrics}
            tier={diffResult?.tier}
            strategy={strategy}
            autoStrategy={autoStrategy}
            onStrategyChange={setStrategy}
            showHeatmap={showHeatmap}
            onToggleHeatmap={setShowHeatmap}
            onAccept={() => { void onAcceptChanges(); }}
            onDiscard={onDiscardChanges}
            onRegenerate={onRegenerateSameParams}
            onVersionPick={(v) => navigate(`/edit/${v.hash_id}/1`)}
            accepting={accepting}
            analyzing={analyzing}
            maskPainted={maskPainted}
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
            maskMethod={maskMethod || "native"}
            templateOpen={templateOpen}
            setTemplateOpen={setTemplateOpen}
            templateLocked={templateLocked}
            setTemplateLocked={setTemplateLocked}
            customTemplate={customTemplate}
            setCustomTemplate={setCustomTemplate}
            maskPainted={maskPainted}
            onPickNode={onPickNode}
          />
        )}
      </div>
      <StatusBar
        zoom={zoomDisplay}
        dim={`${imageW}×${imageH}`}
        cursor={`(${cursorXY.x}, ${cursorXY.y})`}
        brush={Math.round(brushOpts.size)}
        hint={
          locked
            ? "generating · controls locked until complete or cancelled"
            : (modelNotice || restoreNote || validationMessage || modeHint || statusHint)
        }
        onHelp={() => setShowCheat(true)}
      />
      {validationMessage && !showingCompare && (
        <div style={{ position: "absolute", left: 89, top: 92, zIndex: 12 }}>
          <ValidationBanner message={validationMessage} />
        </div>
      )}
      {locked && (selectedModel?.capabilities?.partial_images_max ?? 0) >= 1 && (
        <CanvasPartial
          partialUrl={partials[partials.length - 1]?.url || null}
          partialIndex={Math.min(partials.length || 1, 2)}
          partialMax={2}
          elapsedSec={submitStartedAt ? (Date.now() - submitStartedAt) / 1000 : 0}
        />
      )}
      {locked && (
        <>
          <LockVeil />
          {/* Back-only top bar that floats above the lock veil so the
              user always has an out — leaving while a job is in flight
              is fine, the work is server-side and reachable from
              archive. */}
          <div className="me-back-overlay">
            <button
              type="button"
              onClick={() => {
                clearDraft({ silent: true }).finally(() => navigate("/archive"));
              }}
              title="Back to archive"
              data-testid="me-lock-back"
            >
              <MEIcon name="back" size={18} />
            </button>
          </div>
          <GeneratingCard
            startedAt={submitStartedAt || Date.now()}
            avgSec={getEstimatedDuration(
              selectedModel?.model_id || sourceJob?.model || "default"
            )}
            phase={status}
            partials={partials}
            supportsPartial={
              (selectedModel?.capabilities?.partial_images_max ?? 0) >= 1
            }
            prompt={prompt}
            modelId={selectedModel?.model_id || sourceJob?.model || "—"}
            quality={advanced.quality}
            maskCoveragePct={
              maskCanvasRef && maskPainted
                ? Math.round(maskPaintedRatio(maskCanvasRef) * 100)
                : null
            }
            seqNo={resultJob?.seq_no || null}
          />
        </>
      )}
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
      <captchaGate.Modal />
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
