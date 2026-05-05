import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../components/Icon.jsx";
import TopBar from "../components/TopBar.jsx";
import TurnstileModal from "../components/TurnstileModal.jsx";
import SizeCustomModal from "../components/SizeCustomModal.jsx";
import DraftToast from "../components/DraftToast.jsx";
import { getModels } from "../api/models.js";
import { createJob, precheck as precheckJob } from "../api/jobs.js";
import { createSession } from "../api/sessions.js";
import * as sseStore from "../store/sse.js";
import * as archiveStore from "../store/archive.js";
import { usePreferences } from "../store/preferences.js";
import { useAuth } from "../store/auth.js";
import { useDraftAutosave } from "../hooks/useDraftAutosave.js";

// ---------------------------------------------------------------------------
// Visual atoms
//
// Logos are keyed off the `logo` string the backend sends in each model
// descriptor. Anything we don't recognise renders as the `draft` placeholder
// so a brand-new model isn't invisible while design copy catches up.
// ---------------------------------------------------------------------------

function ModelLogo({ kind, size = 22 }) {
  const box = {
    width: size,
    height: size,
    border: "1px solid var(--ink)",
    background: "#fffdf7",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    overflow: "hidden",
  };
  if (kind === "chatgpt") {
    return (
      <span style={{ ...box, background: "var(--ink)" }}>
        <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 16 16" fill="none">
          <path
            d="M8 1.5l5.5 3.2v6.6L8 14.5l-5.5-3.2V4.7L8 1.5z"
            stroke="var(--banana)"
            strokeWidth="1.3"
          />
          <path d="M5 6l3 1.7L11 6M8 7.7v4.5" stroke="var(--banana)" strokeWidth="1.3" />
        </svg>
      </span>
    );
  }
  if (kind === "flash") {
    return (
      <span style={{ ...box, background: "var(--banana)" }}>
        <svg width={size * 0.5} height={size * 0.6} viewBox="0 0 10 14" fill="none">
          <path d="M5.5 1L1 8h3l-1 5 5-7H5l1-5z" fill="var(--ink)" />
        </svg>
      </span>
    );
  }
  return (
    <span style={box}>
      <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 16 16" fill="none">
        <rect
          x="2.5"
          y="2.5"
          width="11"
          height="11"
          stroke="var(--ink)"
          strokeWidth="1.3"
          strokeDasharray="2 1.5"
        />
        <path d="M5 8h6M8 5v6" stroke="var(--ink)" strokeWidth="1.3" />
      </svg>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Aspect ratio rendering — keep the proportional preview the original
// mockup had. We compute a small box (max 36×32) from the ratio string
// so any value the backend allows can be drawn without a hardcoded list.
// ---------------------------------------------------------------------------

// "12s ago" / "3m ago" / "5d ago" — used by the session picker so the
// list reads the same as the original mockup ("updated 12m ago"). We
// keep it intentionally tiny: anything older than a month collapses to
// the month count, and we never show absolute dates.
function relativeTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `updated ${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `updated ${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `updated ${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 7) return `updated ${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 5) return `updated ${w}w ago`;
  const mo = Math.floor(d / 30);
  return `updated ${mo}mo ago`;
}

function aspectBoxSize(ratio) {
  const parts = String(ratio || "1:1").split(":");
  const a = Math.max(1, Number(parts[0]) || 1);
  const b = Math.max(1, Number(parts[1]) || 1);
  // 36×32 fits the chip layout. Long ratios like 8:1 collapse below
  // the minimum so we floor at 4 px to keep them visible.
  const maxW = 36;
  const maxH = 32;
  const ratioVal = a / b;
  let w = maxW;
  let h = maxW / ratioVal;
  if (h > maxH) {
    h = maxH;
    w = maxH * ratioVal;
  }
  return { w: Math.max(4, Math.round(w)), h: Math.max(4, Math.round(h)) };
}

// ---------------------------------------------------------------------------
// Param helpers — scrub a value off the request when the new model's
// capability set no longer permits it. Without this, switching from
// gpt-image-2 (size=1024x1024) to gemini-3.1-flash would leak the
// OpenAI-only `size` field into the gemini payload and trigger 422.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Param helpers — schema-driven. Walk every field declared by the model's
// ui_schema and drop / clamp values that fall outside the merged
// capabilities. Without this, switching from gpt-image-2 (size=1024x1024)
// to gemini-3.1-flash would leak the OpenAI-only `size` field into the
// gemini payload and trigger 422.
//
// `value_key` indirection: a field's `k` names the *capability* the cell
// reads from (e.g. n_max), but the actual *param* it writes to may be a
// different key (n). The fallback is `k` for the common case where they
// match (size, aspect_ratio, quality, …).
//
// Field-key universe: anything outside the schema's value_keys plus a
// hard-coded set of submission-glue keys (model / prompt / etc.) is
// considered junk left over from a previous model and gets dropped on
// reconcile. Without this gate, default user-prefs like
// `aspect_ratio="1:1"` survive a switch to gpt-image-2 (which has no
// aspect_ratio field) and leak into the request payload.
// ---------------------------------------------------------------------------

const SUBMISSION_GLUE_KEYS = new Set([
  "model",
  "prompt",
  "session_id",
  "client_request_id",
  "captcha_token",
]);

function paramKey(field) {
  return field.value_key || field.k;
}

function reconcileParams(params, capabilities, uiSchema) {
  const next = { ...params };
  const allowedParamKeys = new Set(SUBMISSION_GLUE_KEYS);
  for (const field of uiSchema || []) {
    allowedParamKeys.add(paramKey(field));
  }

  // Drop any param that no longer corresponds to a schema field — this
  // is what kills the gpt-image-2 ← gemini aspect_ratio leak.
  for (const k of Object.keys(next)) {
    if (!allowedParamKeys.has(k)) {
      delete next[k];
    }
  }

  for (const field of uiSchema || []) {
    const cap = capabilities?.[field.k];
    const pk = paramKey(field);
    const cur = next[pk];

    if (
      field.control === "chip-grid" ||
      field.control === "chip-row" ||
      field.control === "select"
    ) {
      // Drop the value when the merged caps don't expose this field at
      // all (cap null/undefined/non-array) OR when the current value
      // isn't in the allowed list. The first arm matters because model
      // defaults (e.g. background="auto" for gpt-image-2) get applied
      // unconditionally — if no provider opts the field in, the UI
      // renders it disabled and the user can't clear the value
      // themselves, but the default still ends up on the wire and
      // trips the backend's INVALID_PARAMETER guard.
      //
      // Special case: ``size`` is allowed to hold a value outside the
      // chip list when ``capabilities.size_allow_custom`` is true. The
      // backend validator will check the custom format itself; we just
      // need to keep the value alive across re-renders.
      const isCustomSizeOptIn =
        field.k === "size" && capabilities?.size_allow_custom === true;
      if (
        cur != null &&
        !isCustomSizeOptIn &&
        (!Array.isArray(cap) || !cap.includes(cur))
      ) {
        delete next[pk];
      }
    } else if (field.control === "number") {
      if (typeof cur === "number" && typeof cap === "number" && cur > cap) {
        next[pk] = cap;
      }
    } else if (field.control === "toggle") {
      if (cur === true && cap !== true) {
        next[pk] = false;
      }
    }
  }
  return next;
}

function applyDefaults(defaults, params, capabilities, uiSchema) {
  // Apply backend-recommended defaults only when the field is unset
  // *or* the previous value is no longer allowed. We lean to "preserve
  // user intent" so switching models keeps the prompt et al intact.
  const next = { ...params };
  const setIfMissing = (key, fallback) => {
    if (next[key] === undefined || next[key] === null) next[key] = fallback;
  };
  Object.entries(defaults || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined) setIfMissing(k, v);
  });
  return reconcileParams(next, capabilities, uiSchema);
}

// Build a render plan from (uiSchema, capabilities). For each field,
// decide whether it's interactive at all and which individual options
// are reachable under the user's current tier × provider mix. The
// renderer uses this directly — it never re-derives from capabilities.
function useFieldRenderPlan(uiSchema, capabilities) {
  return useMemo(() => {
    const plan = (uiSchema || []).map((field) => {
      const cap = capabilities?.[field.k];
      let allowedOptions = null;
      let fieldDisabled = false;
      let disabledReason = null;

      if (
        field.control === "chip-grid" ||
        field.control === "chip-row" ||
        field.control === "select"
      ) {
        if (!Array.isArray(cap) || cap.length === 0) {
          fieldDisabled = true;
          disabledReason = "Not available on your current tier.";
          allowedOptions = new Set();
        } else {
          allowedOptions = new Set(cap);
        }
      } else if (field.control === "number") {
        const max = typeof cap === "number" ? cap : null;
        const lowerBound = field.min ?? 1;
        if (max == null || max <= lowerBound) {
          // max == null means the merged caps don't expose an upper
          // bound → still interactive but driven by the schema's max.
          if (max != null && max < lowerBound) {
            fieldDisabled = true;
            disabledReason = "Not adjustable for this model.";
          } else if (max != null && max === lowerBound && (field.presets?.length ?? 0) <= 1) {
            // Locked to a single value (e.g. Gemini n=1) — still
            // render so the layout is stable, but no presets to pick.
            fieldDisabled = false;
          }
        }
      } else if (field.control === "toggle") {
        if (cap !== true) {
          fieldDisabled = true;
          disabledReason =
            cap === false
              ? "Provider has not enabled this feature."
              : "Not available on your current tier.";
        }
      }

      return { field, allowedOptions, fieldDisabled, disabledReason };
    });

    const byOrder = (a, b) => (a.field.order ?? 100) - (b.field.order ?? 100);
    const primary = plan
      .filter((p) => (p.field.group ?? "primary") === "primary")
      .sort(byOrder);
    const advanced = plan
      .filter((p) => p.field.group === "advanced")
      .sort(byOrder);
    return { primary, advanced };
  }, [uiSchema, capabilities]);
}

// ---------------------------------------------------------------------------
// CreatePage
// ---------------------------------------------------------------------------

export default function CreatePage() {
  const navigate = useNavigate();
  const { prefs: userPrefs } = usePreferences();
  const { user } = useAuth();
  const userId = user?.id || null;
  // Snapshot the user's "default ratio / batch size / model" once on
  // mount so changing them in /settings while the page is open does
  // not silently reset whatever the user has already configured here.
  const initialPrefsRef = useRef(null);
  if (initialPrefsRef.current === null) {
    initialPrefsRef.current = {
      aspect_ratio: userPrefs?.generation?.default_aspect_ratio || null,
      batch_size: userPrefs?.generation?.default_batch_size || 1,
      model_id: userPrefs?.generation?.default_model_id || null,
    };
  }

  // Catalog state (from /api/models). `null` while we wait for the first load.
  const [catalog, setCatalog] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [selectedModel, setSelectedModel] = useState(null);

  // Form state. Seed params with the user's preferred defaults so a
  // power user with "ratio=3:2, batch=2" set never has to re-pick
  // them on every visit.
  const [prompt, setPrompt] = useState("");
  const [params, setParams] = useState(() => {
    const seed = {};
    const initial = initialPrefsRef.current;
    if (initial.aspect_ratio) seed.aspect_ratio = initial.aspect_ratio;
    if (initial.batch_size && initial.batch_size > 1) seed.n = initial.batch_size;
    return seed;
  });
  const [refs, setRefs] = useState([]); // array of File objects (insertion order)
  const [sessionId, setSessionId] = useState(null);

  // Autosave & restore — applied via the useDraftAutosave hook below.
  // Restore lands one snapshot of state into the form before any user
  // interaction. We stash a pending model id (the schema-defaults
  // useEffect later picks it up) so it doesn't fight the catalog loader.
  const pendingRestoreModelIdRef = useRef(null);
  const handleRestoreDraft = useCallback((restored) => {
    if (typeof restored.prompt === "string") setPrompt(restored.prompt);
    if (Array.isArray(restored.refs)) setRefs(restored.refs);
    if (restored.params && typeof restored.params === "object") {
      setParams(restored.params);
    }
    if (typeof restored.sessionId === "string" || restored.sessionId === null) {
      setSessionId(restored.sessionId);
    }
    if (restored.modelId) {
      pendingRestoreModelIdRef.current = restored.modelId;
    }
  }, []);

  // Submission state.
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [showTurnstile, setShowTurnstile] = useState(false);
  const [turnstileSiteKey, setTurnstileSiteKey] = useState(null);
  const [showSizeCustom, setShowSizeCustom] = useState(false);
  const pendingSubmitRef = useRef(null); // payload waiting for cf token
  const lastFetchAtRef = useRef(0);
  const clientRequestIdRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getModels();
      setCatalog(data);
      lastFetchAtRef.current = Date.now();
      setLoadError("");
      // If the currently-selected model disappeared (admin disabled a
      // provider), fall back to the user's preferred default — and
      // then to the first available model when that's unavailable.
      const all = data?.models || [];
      const stillThere = all.find(
        (m) => m.model_id === (selectedModel?.model_id || "")
      );
      if (!stillThere) {
        // Restored draft model takes precedence over the user's default
        // pref so a refresh lands the user back on the model they were
        // last editing with.
        const restoredId = pendingRestoreModelIdRef.current;
        const restored = restoredId
          ? all.find((m) => m.model_id === restoredId && m.available)
          : null;
        if (restored) pendingRestoreModelIdRef.current = null;
        const preferredId = initialPrefsRef.current?.model_id;
        const preferred = preferredId
          ? all.find((m) => m.model_id === preferredId && m.available)
          : null;
        const firstOk = all.find((m) => m.available) || all[0] || null;
        setSelectedModel(restored || preferred || firstOk);
      } else {
        setSelectedModel(stillThere);
      }
    } catch (err) {
      setLoadError(err?.message || "Failed to load models.");
    }
  }, [selectedModel?.model_id]);

  // Load on mount.
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-pull when the admin SSE event says capabilities changed.
  useEffect(() => {
    const off = sseStore.subscribe("model_capabilities_changed", () => refresh());
    return off;
  }, [refresh]);

  // Apply the new model's defaults whenever the active model changes.
  useEffect(() => {
    if (!selectedModel) return;
    setParams((prev) =>
      applyDefaults(
        selectedModel.defaults,
        prev,
        selectedModel.capabilities,
        selectedModel.ui_schema
      )
    );
  }, [selectedModel?.model_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Draft autosave + restore. Driven entirely client-side; no backend
  // calls. The hook waits for the catalog before kicking in so the
  // restored model_id can be validated against the user's tier.
  const { toast: draftToast, clearDraft } = useDraftAutosave({
    userId,
    prompt,
    params,
    refs,
    modelId: selectedModel?.model_id || null,
    sessionId,
    catalog,
    enabled: !!catalog && !!userId,
    onRestore: handleRestoreDraft,
  });

  // 60s freshness rule: if the user lingers, re-pull on the next
  // Generate attempt so a capability tweak that landed mid-session
  // doesn't surface as a 422.
  const ensureFresh = useCallback(async () => {
    if (Date.now() - lastFetchAtRef.current > 60_000) {
      await refresh();
    }
  }, [refresh]);

  // -----------------------------------------------------------------
  // Submission flow
  // -----------------------------------------------------------------

  const buildPayload = useCallback(() => {
    if (!selectedModel) return null;
    // Strip undefined / null so the JSON we ship is minimal.
    const cleaned = {};
    Object.entries(params).forEach(([k, v]) => {
      if (v === undefined || v === null) return;
      if (typeof v === "string" && v === "") return;
      cleaned[k] = v;
    });
    cleaned.model = selectedModel.model_id;
    cleaned.prompt = prompt;
    if (sessionId) cleaned.session_id = sessionId;
    if (!clientRequestIdRef.current) {
      clientRequestIdRef.current = `req_${Math.random()
        .toString(36)
        .slice(2, 14)}_${Date.now().toString(36)}`;
    }
    cleaned.client_request_id = clientRequestIdRef.current;
    return cleaned;
  }, [selectedModel, params, prompt, sessionId]);

  const submit = useCallback(
    async (captchaToken) => {
      if (!selectedModel) return;
      const payload = buildPayload();
      if (!payload) return;
      if (captchaToken) payload.captcha_token = captchaToken;

      setSubmitting(true);
      setSubmitError(null);
      try {
        const response = await createJob({ payload, references: refs });
        // Hand off to Archive synchronously — Archive prepends from
        // sessionStorage on mount before any SSE event arrives.
        // FE-04: feed the response straight into the archive store so the
        // page we navigate to renders the QUEUED card immediately.
        // The store also persists it to IndexedDB so a reload survives.
        await archiveStore.insertOptimistic(response);
        // Reset the request id so the next Generate gets a fresh one.
        clientRequestIdRef.current = null;
        // Silent — user's eyes are about to follow the navigate().
        clearDraft({ silent: true });
        navigate("/archive");
      } catch (err) {
        if (err?.code === "CAPTCHA_REQUIRED") {
          // Precheck said no captcha but the backend's gate disagreed
          // (rolling counter crossed mid-flight). Re-run precheck so
          // the user sees the modal.
          try {
            const fresh = await precheckJob({ model: selectedModel.model_id });
            if (fresh.captcha_required) {
              setTurnstileSiteKey(fresh.site_key || null);
              pendingSubmitRef.current = payload;
              setShowTurnstile(true);
              setSubmitError(null);
            } else {
              setSubmitError(err);
            }
          } catch (e2) {
            setSubmitError(e2);
          }
        } else {
          setSubmitError(err);
        }
      } finally {
        setSubmitting(false);
      }
    },
    [buildPayload, clearDraft, navigate, refs, selectedModel]
  );

  const handleGenerate = useCallback(async () => {
    if (!selectedModel) return;
    setSubmitError(null);
    if (submitting) return;
    if (!prompt.trim()) {
      setSubmitError(new Error("Prompt is required."));
      return;
    }

    await ensureFresh();
    try {
      const check = await precheckJob({ model: selectedModel.model_id });
      if (check?.captcha_required) {
        setTurnstileSiteKey(check.site_key || null);
        pendingSubmitRef.current = null;
        setShowTurnstile(true);
        return;
      }
    } catch (err) {
      // Precheck shouldn't fail under normal conditions; surface and
      // refuse to submit so the user isn't stuck guessing.
      setSubmitError(err);
      return;
    }
    await submit(null);
  }, [ensureFresh, prompt, selectedModel, submit, submitting]);

  const onCaptchaSuccess = useCallback(
    async (token) => {
      setShowTurnstile(false);
      // Two paths land here:
      //   1. Initial precheck said captcha → we hadn't built a payload yet.
      //   2. createJob threw CAPTCHA_REQUIRED mid-flight → payload was
      //      stashed in pendingSubmitRef.
      const stashed = pendingSubmitRef.current;
      pendingSubmitRef.current = null;
      if (stashed) {
        // Re-submit using the same client_request_id so we don't double-charge.
        const enriched = { ...stashed, captcha_token: token };
        setSubmitting(true);
        try {
          const response = await createJob({ payload: enriched, references: refs });
          // FE-04: feed the response straight into the archive store so
          // the page we navigate to renders the QUEUED card immediately.
          // The store also persists it to IndexedDB so a reload survives.
          await archiveStore.insertOptimistic(response);
          clientRequestIdRef.current = null;
          clearDraft({ silent: true });
          navigate("/archive");
        } catch (err) {
          setSubmitError(err);
        } finally {
          setSubmitting(false);
        }
      } else {
        await submit(token);
      }
    },
    [clearDraft, navigate, refs, submit]
  );

  // -----------------------------------------------------------------
  // Reference upload helpers
  // -----------------------------------------------------------------

  const fileInputRef = useRef(null);
  const acceptRefs = useCallback(
    (fileList) => {
      const incoming = Array.from(fileList || []).filter(Boolean);
      const max = selectedModel?.capabilities?.max_reference_images;
      const cap = typeof max === "number" ? max : 14;
      setRefs((prev) => [...prev, ...incoming].slice(0, cap));
    },
    [selectedModel?.capabilities?.max_reference_images]
  );

  const removeRef = useCallback((idx) => {
    setRefs((prev) => prev.filter((_, i) => i !== idx));
  }, []);
  const clearRefs = useCallback(() => setRefs([]), []);

  // Inline session create. Window-prompt is intentionally minimal — the
  // session can always be renamed from the archive page later. We re-fetch
  // /api/models so the new entry shows up in this page's session picker
  // alongside whatever the user already had.
  const onCreateSession = useCallback(async () => {
    const name = window.prompt("Name this session:");
    const trimmed = (name || "").trim();
    if (!trimmed) return;
    try {
      const created = await createSession(trimmed);
      await refresh();
      setSessionId(created.id);
    } catch (err) {
      setSubmitError(err);
    }
  }, [refresh]);

  // -----------------------------------------------------------------
  // Derived view-model
  //
  // Param-panel fields are now driven entirely by the model's ui_schema
  // (see useFieldRenderPlan / SchemaParamsPanel below). The few caps we
  // still pluck inline are page-chrome bits the schema doesn't cover:
  // the Output count ticker shown in the Generate button, the Reference
  // upload limit, and the Prompt textarea cap.
  // -----------------------------------------------------------------

  const caps = selectedModel?.capabilities || {};
  const maxN = typeof caps.n_max === "number" ? caps.n_max : 1;
  const promptCharCap =
    typeof caps.max_prompt_chars === "number" ? caps.max_prompt_chars : 32_000;

  const setParam = (key, value) =>
    setParams((prev) => ({ ...prev, [key]: value }));

  const sessions = catalog?.sessions || [];
  const isEmpty = refs.length === 0;
  const generateCount = useMemo(() => {
    if (maxN <= 1) return 1;
    const n = params.n || 1;
    return n > maxN ? maxN : n;
  }, [maxN, params.n]);

  const errorMessage = submitError
    ? submitError.message || "Generation failed."
    : null;

  // Schema-driven render plan for the right-rail param panel.
  const fieldPlan = useFieldRenderPlan(
    selectedModel?.ui_schema || [],
    selectedModel?.capabilities || {}
  );

  // -----------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------

  return (
    <div
      style={{
        flex: 1,
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      <TopBar
        crumb="Create / Single Job"
        title={
          <>
            Make a <span style={{ fontStyle: "italic" }}>thing</span>.
          </>
        }
        subtitle="Text prompt, up to 14 reference images, any provider."
        right={
          <>
            <DraftToast value={draftToast} />
            <button
              className="btn sm ghost"
              onClick={() => {
                setPrompt("");
                clearRefs();
                clientRequestIdRef.current = null;
                clearDraft();
              }}
            >
              Clear
            </button>
          </>
        }
      />

      {loadError ? (
        <div
          className="mono"
          style={{
            padding: "12px 32px",
            color: "#c0392b",
            background: "#fdecea",
            borderBottom: "1px solid #c0392b",
            fontSize: 12,
          }}
        >
          {loadError} <button onClick={refresh} className="btn xs">Retry</button>
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          // ``minmax(0, 1fr)`` is the magic that stops the left column's
          // intrinsic width (long model names, long size chips) from
          // spilling into — and thereby clipping — the right column.
          // The default ``1fr`` is shorthand for ``minmax(auto, 1fr)``
          // and ``auto`` is "as wide as the content needs"; that's the
          // root cause of the overflow we saw in the first screenshots.
          gridTemplateColumns: "minmax(0, 1fr) 360px",
          flex: 1,
          minHeight: 0,
        }}
      >
        <div
          style={{
            padding: "20px 32px 24px",
            borderRight: "1px solid var(--ink)",
            display: "flex",
            flexDirection: "column",
            gap: 18,
            minHeight: 0,
            minWidth: 0,
            overflowY: "auto",
          }}
        >
          {/* Prompt */}
          <div
            style={{
              border: "1px solid var(--ink)",
              background: "#fffdf7",
              flex: 1,
              minHeight: 180,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 14px",
                borderBottom: "1px solid var(--ink)",
                background: "var(--paper-2)",
                flexShrink: 0,
              }}
            >
              <div className="mono caps" style={{ fontSize: 10 }}>
                § Prompt
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                {/* The two left chips are decorative for now — they
                    sat in the original mockup as future hooks. The
                    char counter on the right is live and clamps to
                    ``promptCharCap``. */}
                <button className="chip">ENHANCE</button>
                <button className="chip">TEMPLATES</button>
                <button className="chip">{prompt.length} CHARS</button>
              </div>
            </div>
            <textarea
              value={prompt}
              onChange={(e) =>
                setPrompt(e.target.value.slice(0, promptCharCap))
              }
              style={{
                width: "100%",
                flex: 1,
                border: "none",
                outline: "none",
                padding: "16px 22px",
                fontFamily: "var(--font-display)",
                fontSize: 20,
                lineHeight: 1.4,
                background: "transparent",
                color: "var(--ink)",
                letterSpacing: "-0.01em",
                resize: "vertical",
                minHeight: 110,
                display: "block",
              }}
              placeholder="A still-life of three overripe bananas on a marble slab, flemish painting, warm window light, shallow depth of field, 35mm"
            />
            <div
              style={{
                padding: "6px 14px",
                borderTop: "1px dashed var(--rule-2)",
                background: "var(--paper)",
                display: "flex",
                justifyContent: "space-between",
                fontSize: 10,
                color: "var(--ink-3)",
                flexShrink: 0,
              }}
            >
              <span className="mono">
                Tip: mention <b>subject · style · light · composition · lens</b>
              </span>
              <span className="mono">⌘↵ to generate</span>
            </div>
          </div>

          {/* Reference images */}
          <div style={{ display: "flex", flexDirection: "column", flexShrink: 0 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: 10,
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span
                  className="display"
                  style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em" }}
                >
                  Reference images
                </span>
                <span className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                  {refs.length} /{" "}
                  {typeof caps.max_reference_images === "number"
                    ? caps.max_reference_images
                    : 14}{" "}
                  · OPTIONAL
                </span>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="chip" onClick={clearRefs}>
                  <Icon name="close" size={9} /> CLEAR
                </button>
                <button className="chip" onClick={() => fileInputRef.current?.click()}>
                  <Icon name="upload" size={10} /> UPLOAD
                </button>
                {/* Decorative — Cmd-V already pastes any image into the
                    upload input via the browser, but the chip carries
                    over from the original mockup as a discoverability
                    hint. */}
                <button className="chip">PASTE ⌘V</button>
              </div>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/png,image/jpeg,image/webp"
              style={{ display: "none" }}
              onChange={(e) => {
                acceptRefs(e.target.files);
                e.target.value = "";
              }}
            />

            {isEmpty ? (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
                  gap: 8,
                  // ``alignItems: stretch`` (default) + a square aspect
                  // on the placeholder cells means the row's height is
                  // dictated by the placeholders. The CTA below has no
                  // aspectRatio of its own; ``height: 100%`` lets it
                  // grow to match. This is what kept the original
                  // mockup's 4×1 + 1×1 cells flush with each other.
                }}
              >
                <div
                  style={{
                    gridColumn: "1 / 5",
                    height: "100%",
                    border: "1px dashed var(--ink)",
                    background:
                      "repeating-linear-gradient(45deg, transparent 0 10px, rgba(0,0,0,0.02) 10px 11px), #fffdf7",
                    display: "flex",
                    alignItems: "center",
                    gap: 14,
                    padding: "0 18px",
                    position: "relative",
                  }}
                >
                  {/* Three rotated color tiles — pure decoration, copies the
                      original mockup. Sat directly to the left of the copy
                      block; ``flexShrink: 0`` keeps them from collapsing
                      when the cell narrows. */}
                  <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                    {["#E8D5B0", "#C9B894", "#A89878"].map((c, i) => (
                      <div
                        key={c}
                        style={{
                          width: 28,
                          height: 28,
                          background: c,
                          border: "1px solid var(--ink)",
                          transform: `rotate(${(i - 1) * 4}deg)`,
                          boxShadow: "1px 1px 0 var(--ink)",
                        }}
                      />
                    ))}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      className="display"
                      style={{
                        fontSize: 16,
                        fontWeight: 700,
                        letterSpacing: "-0.02em",
                        color: "var(--ink)",
                        lineHeight: 1.15,
                      }}
                    >
                      Drop reference imagery{" "}
                      <span style={{ fontStyle: "italic", color: "var(--ink-3)" }}>— or skip.</span>
                    </div>
                    <div
                      className="mono"
                      style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 4 }}
                    >
                      Up to {typeof caps.max_reference_images === "number"
                        ? caps.max_reference_images
                        : 14}{" "}
                      images. Optional — pure-text prompts work great.
                    </div>
                  </div>
                  <button
                    className="btn sm"
                    style={{ flexShrink: 0 }}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Icon name="upload" size={11} /> Choose files
                  </button>
                </div>
                {[...Array(3)].map((_, i) => (
                  <div
                    key={i}
                    style={{
                      aspectRatio: "1/1",
                      border: "1px dashed var(--ink-4)",
                      background: "transparent",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 4,
                      opacity: 0.5,
                    }}
                  >
                    <Icon name="plus" size={12} stroke="var(--ink-4)" />
                    <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>
                      {i + 1}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8 }}>
                {refs.map((file, i) => (
                  <div
                    key={`${file.name}-${i}`}
                    style={{
                      aspectRatio: "1/1",
                      border: "1px solid var(--ink)",
                      background: "var(--paper-3)",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    <RefThumb file={file} />
                    <div
                      style={{
                        position: "absolute",
                        bottom: 0,
                        left: 0,
                        right: 0,
                        padding: "3px 6px",
                        background: "var(--ink)",
                        color: "var(--paper)",
                        fontSize: 9,
                        fontFamily: "var(--font-mono)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {i + 1} · {file.name}
                    </div>
                    <button
                      onClick={() => removeRef(i)}
                      style={{
                        position: "absolute",
                        top: 4,
                        right: 4,
                        width: 18,
                        height: 18,
                        background: "var(--ink)",
                        color: "var(--paper)",
                        border: "none",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <Icon name="close" size={8} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Model picker */}
          <div style={{ flexShrink: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 10,
              }}
            >
              <div
                className="display"
                style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em" }}
              >
                Model
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                which engine renders this
              </div>
            </div>
            {/* Single-row horizontally-scrolling model strip. The
                container hides its scrollbar at rest and reveals a
                themed thin track on hover/focus (see ``.model-strip``
                rules at the bottom of this file). Cards are fixed-width
                so the row never reflows when the catalogue grows. */}
            <div className="model-strip" tabIndex={-1}>
              {/* Sort available models first so the picker reads cleanly:
                  the cards the user can actually click sit on the left,
                  greyed-out variants follow. Stable alphabetical within
                  each group keeps the order deterministic across
                  refreshes. */}
              {(catalog?.models || [])
                .slice()
                .sort((a, b) => {
                  if (a.available !== b.available) return a.available ? -1 : 1;
                  return a.model_id.localeCompare(b.model_id);
                })
                .map((m) => {
                const on = selectedModel?.model_id === m.model_id;
                const disabled = !m.available;
                return (
                  <button
                    key={m.model_id}
                    onClick={() => !disabled && setSelectedModel(m)}
                    disabled={disabled}
                    title={disabled ? unavailableReasonCopy(m.available_reason) : undefined}
                    style={{
                      padding: 10,
                      background: on ? "var(--ink)" : disabled ? "var(--paper-3)" : "#fffdf7",
                      color: on ? "var(--paper)" : "var(--ink)",
                      border: "1px solid var(--ink)",
                      cursor: disabled ? "not-allowed" : "pointer",
                      textAlign: "left",
                      position: "relative",
                      display: "flex",
                      gap: 10,
                      alignItems: "flex-start",
                      opacity: disabled ? 0.55 : 1,
                      flex: "0 0 280px",
                      minWidth: 0,
                    }}
                  >
                    <ModelLogo kind={m.logo} size={22} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 6,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 12,
                            fontWeight: 700,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {m.display_name}
                        </span>
                        {m.tag ? (
                          <span
                            className="mono"
                            style={{
                              fontSize: 8,
                              padding: "1px 5px",
                              letterSpacing: "0.1em",
                              flexShrink: 0,
                              background: on ? "var(--banana)" : "var(--ink)",
                              color: on ? "var(--ink)" : "var(--banana)",
                            }}
                          >
                            {m.tag}
                          </span>
                        ) : null}
                      </div>
                      <div
                        style={{
                          fontSize: 10,
                          lineHeight: 1.4,
                          marginTop: 4,
                          color: on ? "var(--paper-2)" : "var(--ink-3)",
                        }}
                      >
                        {disabled
                          ? unavailableReasonCopy(m.available_reason)
                          : m.blurb || ""}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Bind to session — always rendered. The "+ New session" tile
              gives the user a way to create one without leaving the page;
              it window-prompts for a name and POSTs /api/sessions, then
              re-pulls /api/models so the new session shows up here. */}
          <div style={{ flexShrink: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 10,
              }}
            >
              <div
                className="display"
                style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em" }}
              >
                Bind to session
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                group this run with related work
              </div>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
                gap: 6,
              }}
            >
              {sessions.map((s) => {
                const bound = sessionId === s.id;
                return (
                  <label
                    key={s.id}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 4,
                      padding: "10px 11px",
                      background: bound ? "var(--banana-soft)" : "#fffdf7",
                      border: "1px solid var(--ink)",
                      cursor: "pointer",
                      minWidth: 0,
                      // Pin a stable card height so the populated cards
                      // and the dashed "+ New session" tile line up at
                      // the same baseline regardless of session count.
                      minHeight: 60,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        minWidth: 0,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={bound}
                        onChange={() => setSessionId(bound ? null : s.id)}
                        style={{ flexShrink: 0 }}
                      />
                      <span
                        style={{
                          fontSize: 12,
                          fontWeight: 600,
                          flex: 1,
                          minWidth: 0,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {s.name}
                      </span>
                      <span
                        className="mono"
                        style={{ fontSize: 9, color: "var(--ink-3)", flexShrink: 0 }}
                      >
                        {s.image_count}
                      </span>
                    </div>
                    <span
                      className="mono"
                      style={{ fontSize: 9, color: "var(--ink-4)", paddingLeft: 20 }}
                    >
                      {relativeTime(s.updated_at)}
                    </span>
                  </label>
                );
              })}
              <button
                onClick={onCreateSession}
                style={{
                  padding: "10px 11px",
                  background: "transparent",
                  border: "1px dashed var(--ink-3)",
                  cursor: "pointer",
                  fontSize: 11,
                  color: "var(--ink-3)",
                  textAlign: "left",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  justifyContent: "center",
                  minWidth: 0,
                  // Match the populated card height so a row containing
                  // only this tile doesn't read as a stunted half-card.
                  minHeight: 60,
                }}
              >
                <span style={{ fontWeight: 600 }}>+ New session</span>
                <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>
                  start fresh
                </span>
              </button>
            </div>
          </div>
        </div>

        {/* Right column: parameters */}
        <div
          style={{
            background: "var(--paper-2)",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            minWidth: 0,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: "auto",
              padding: "20px 22px",
            }}
          >
            <SchemaParamsPanel
              plan={fieldPlan}
              params={params}
              setParam={setParam}
              capabilities={selectedModel?.capabilities || {}}
              onOpenSizeCustom={() => setShowSizeCustom(true)}
            />
          </div>

          <div
            style={{
              padding: "14px 22px 18px",
              borderTop: "2px solid var(--ink)",
              background: "var(--paper-2)",
              flexShrink: 0,
            }}
          >
            {errorMessage ? (
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  color: "#c0392b",
                  background: "#fdecea",
                  border: "1px solid #c0392b",
                  padding: "6px 8px",
                  marginBottom: 10,
                }}
              >
                {errorMessage}
              </div>
            ) : null}
            <button
              onClick={handleGenerate}
              disabled={submitting || !selectedModel?.available}
              className="btn primary shadowed lg"
              style={{
                width: "100%",
                fontSize: 15,
                opacity: submitting ? 0.6 : 1,
                cursor:
                  submitting || !selectedModel?.available
                    ? "not-allowed"
                    : "pointer",
              }}
            >
              <Icon name="bolt" size={14} />{" "}
              {submitting
                ? "Submitting…"
                : `Generate ×${generateCount}`}
              <span
                className="kbd"
                style={{ marginLeft: "auto", background: "var(--ink)", color: "var(--banana)" }}
              >
                ⌘↵
              </span>
            </button>
          </div>
        </div>
      </div>

      <TurnstileModal
        open={showTurnstile}
        siteKey={turnstileSiteKey}
        onClose={() => {
          setShowTurnstile(false);
          pendingSubmitRef.current = null;
        }}
        onSuccess={onCaptchaSuccess}
      />

      <SizeCustomModal
        open={showSizeCustom}
        initialValue={params.size || null}
        onClose={() => setShowSizeCustom(false)}
        onSelect={(sizeStr) => {
          setParam("size", sizeStr);
          setShowSizeCustom(false);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ChipGroup({
  label,
  hint,
  options,
  value,
  onChange,
  renderOption,
  smallTopMargin = false,
  isOptionAllowed,
  fieldDisabled = false,
  disabledReason = null,
  layout,
  showHairline = true,
}) {
  // Pick a column count by the longest label so chips fit the 360px right
  // panel without spilling. The thresholds below are calibrated against the
  // ticker font (16px / 900) used by the chip body:
  //   - 4 cols ≈ 70px each: comfortable for "1K", "low", "auto", "png"
  //   - 3 cols ≈ 95px each: handles "medium", "high"
  //   - 2 cols ≈ 150px each: fits "1024x1024", "1024x1536", "minimal"
  // ``renderOption`` is only used by the aspect-ratio chips today, whose
  // body is a tiny rectangle plus a 4-char label — those always fit 4-up.
  const longest = options.reduce(
    (acc, opt) => Math.max(acc, String(opt).length),
    0
  );
  let cols;
  if (layout === "row") {
    cols = Math.min(options.length || 1, 4);
  } else if (renderOption) {
    cols = Math.min(options.length || 1, layout === "grid" ? 3 : 4);
  } else if (longest >= 8) {
    cols = 2;
  } else if (longest >= 5) {
    cols = 3;
  } else {
    cols = Math.min(options.length || 1, 4);
  }
  return (
    <div
      style={{
        marginTop: smallTopMargin ? 4 : 0,
        opacity: fieldDisabled ? 0.55 : 1,
      }}
      title={fieldDisabled ? disabledReason || undefined : undefined}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <div
          className="mono caps"
          style={{
            fontSize: 10,
            color: fieldDisabled ? "var(--ink-4)" : "var(--ink-3)",
          }}
        >
          {label}
        </div>
        {hint ? (
          <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            {hint}
          </div>
        ) : null}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          gap: 6,
        }}
      >
        {options.map((opt) => {
          const on = value === opt;
          const allowed =
            !fieldDisabled &&
            (typeof isOptionAllowed === "function" ? isOptionAllowed(opt) : true);
          return (
            <button
              key={opt}
              onClick={() => allowed && onChange(opt)}
              disabled={!allowed}
              title={
                fieldDisabled
                  ? disabledReason || undefined
                  : allowed
                  ? undefined
                  : "Not available on your current tier."
              }
              style={{
                padding: "10px 6px",
                background: on
                  ? "var(--ink)"
                  : allowed
                  ? "#fffdf7"
                  : "var(--paper-3)",
                border: "1px solid var(--ink)",
                color: on
                  ? "var(--banana)"
                  : allowed
                  ? "var(--ink)"
                  : "var(--ink-4)",
                cursor: allowed ? "pointer" : "not-allowed",
                textAlign: "center",
                minWidth: 0,
                overflow: "hidden",
                opacity: allowed ? 1 : 0.5,
              }}
            >
              {renderOption ? (
                renderOption(opt, on)
              ) : (
                <div
                  className="ticker"
                  style={{
                    fontSize: 16,
                    fontWeight: 900,
                    letterSpacing: "-0.03em",
                    lineHeight: 1.1,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {opt}
                </div>
              )}
            </button>
          );
        })}
      </div>
      {fieldDisabled && disabledReason ? (
        <div
          className="mono"
          style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 6 }}
        >
          {disabledReason}
        </div>
      ) : null}
      {showHairline ? <div className="hair" style={{ margin: "18px 0" }} /> : null}
    </div>
  );
}

function Toggle({
  label,
  hint,
  value,
  onChange,
  fieldDisabled = false,
  disabledReason = null,
}) {
  return (
    <label
      title={fieldDisabled ? disabledReason || undefined : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        background: fieldDisabled ? "var(--paper-3)" : "#fffdf7",
        border: "1px solid var(--ink)",
        padding: "8px 10px",
        cursor: fieldDisabled ? "not-allowed" : "pointer",
        opacity: fieldDisabled ? 0.55 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={value}
        disabled={fieldDisabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: fieldDisabled ? "var(--ink-4)" : undefined,
          }}
        >
          {label}
        </div>
        {hint ? (
          <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            {hint}
          </div>
        ) : null}
        {fieldDisabled && disabledReason ? (
          <div
            className="mono"
            style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 2 }}
          >
            {disabledReason}
          </div>
        ) : null}
      </div>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Schema-driven param panel — NumberPresets / FieldRenderer / SchemaParamsPanel.
//
// NumberPresets is intentionally "Output count specific" rather than a
// generic number input: it carries the ticker (×N) visual the original
// mockup used. The plan reserves it for the `n_max` field today; if a
// future schema field also wants this control they get the same look.
// ---------------------------------------------------------------------------

function NumberPresets({
  label,
  hint,
  presets,
  max,
  value,
  onChange,
  fieldDisabled = false,
  disabledReason = null,
}) {
  const display = typeof value === "number" ? value : 1;
  const presetList = presets && presets.length ? presets : [1, 2, 4, 8];
  return (
    <div
      title={fieldDisabled ? disabledReason || undefined : undefined}
      style={{ opacity: fieldDisabled ? 0.55 : 1 }}
    >
      <div
        className="mono caps"
        style={{
          fontSize: 10,
          color: fieldDisabled ? "var(--ink-4)" : "var(--ink-3)",
          marginBottom: 8,
        }}
      >
        {label}
      </div>
      <div
        style={{
          border: "1px solid var(--ink)",
          padding: 12,
          background: fieldDisabled ? "var(--paper-3)" : "#fffdf7",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
          }}
        >
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
            {hint || ""}
          </span>
          <div
            className="ticker"
            style={{ fontSize: 24, fontWeight: 900, letterSpacing: "-0.03em" }}
          >
            ×{display}
          </div>
        </div>
        <div style={{ display: "flex", gap: 4, marginTop: 8 }}>
          {presetList.map((n) => {
            const allowed =
              !fieldDisabled && (typeof max === "number" ? n <= max : true);
            const on = display === n;
            return (
              <button
                key={n}
                onClick={() => allowed && onChange(n)}
                disabled={!allowed}
                title={
                  fieldDisabled
                    ? disabledReason || undefined
                    : allowed
                    ? undefined
                    : `caps at ${max}`
                }
                style={{
                  flex: 1,
                  height: 30,
                  background: on
                    ? "var(--banana)"
                    : allowed
                    ? "transparent"
                    : "var(--paper-3)",
                  border: "1px solid var(--ink)",
                  cursor: allowed ? "pointer" : "not-allowed",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  fontWeight: 700,
                  color: allowed ? "var(--ink)" : "var(--ink-4)",
                  opacity: allowed ? 1 : 0.5,
                }}
              >
                {n}
              </button>
            );
          })}
        </div>
      </div>
      {fieldDisabled && disabledReason ? (
        <div
          className="mono"
          style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 4 }}
        >
          {disabledReason}
        </div>
      ) : null}
    </div>
  );
}

// Field-key-specific cell renderers we still want to keep when migrating
// off the hard-coded JSX. Aspect ratio cells need a proportional preview
// box and image-size cells need a ticker + descriptor — neither fits the
// generic "ticker text" cell ChipGroup ships by default.
const IMAGE_SIZE_NOTES_FOR_RENDERER = {
  512: "preview",
  "1K": "balanced",
  "2K": "print",
  "4K": "max",
};

function aspectRatioRenderOption(opt, on) {
  const { w, h } = aspectBoxSize(opt);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
        color: on ? "var(--banana)" : "var(--ink)",
      }}
    >
      <div
        style={{
          width: w,
          height: h,
          background: on ? "var(--banana)" : "var(--paper-3)",
          border: "1px solid currentColor",
        }}
      />
      <div className="mono" style={{ fontSize: 10, fontWeight: 700 }}>
        {opt}
      </div>
    </div>
  );
}

function imageSizeRenderOption(opt, on) {
  return (
    <div>
      <div
        className="ticker"
        style={{
          fontSize: 20,
          fontWeight: 900,
          letterSpacing: "-0.03em",
          lineHeight: 1,
        }}
      >
        {opt}
      </div>
      <div
        className="mono"
        style={{ fontSize: 9, marginTop: 4, opacity: on ? 0.8 : 0.6 }}
      >
        {IMAGE_SIZE_NOTES_FOR_RENDERER[opt] || ""}
      </div>
    </div>
  );
}

function FieldRenderer({
  plan,
  value,
  onChange,
  capabilities,
  onOpenSizeCustom,
}) {
  const { field, allowedOptions, fieldDisabled, disabledReason } = plan;
  const isAllowed = (opt) =>
    allowedOptions ? allowedOptions.has(opt) : true;

  if (
    field.control === "chip-grid" ||
    field.control === "chip-row" ||
    field.control === "select"
  ) {
    let renderOption;
    if (field.k === "aspect_ratio") renderOption = aspectRatioRenderOption;
    else if (field.k === "image_size") renderOption = imageSizeRenderOption;

    // Size field gets a special "Custom…" affordance when the merged
    // capability surface opts in. The current custom value (if any —
    // i.e. value is set but not in the chip options) renders as a
    // selected pill below the chip grid so users can see / clear it.
    const showSizeCustom =
      field.k === "size" &&
      capabilities?.size_allow_custom === true &&
      typeof onOpenSizeCustom === "function";
    const hasCustomValue =
      field.k === "size" &&
      typeof value === "string" &&
      Array.isArray(field.options) &&
      !field.options.includes(value);

    return (
      <div data-field={field.k}>
        <ChipGroup
          label={field.label}
          hint={field.hint || null}
          options={field.options || []}
          value={value ?? null}
          onChange={onChange}
          renderOption={renderOption}
          layout={field.control === "chip-row" ? "row" : "grid"}
          isOptionAllowed={isAllowed}
          fieldDisabled={fieldDisabled}
          disabledReason={disabledReason}
          showHairline={false}
        />
        {showSizeCustom ? (
          <div
            style={{
              marginTop: 6,
              display: "flex",
              alignItems: "center",
              gap: 6,
              flexWrap: "wrap",
            }}
          >
            {hasCustomValue ? (
              <button
                data-testid="size-custom-current"
                onClick={() => onChange(null)}
                title="Clear custom size"
                className="mono"
                style={{
                  padding: "6px 10px",
                  background: "var(--ink)",
                  color: "var(--paper)",
                  border: "1px solid var(--ink)",
                  fontSize: 11,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span style={{ fontWeight: 700 }}>{value}</span>
                <Icon name="close" size={9} />
              </button>
            ) : null}
            <button
              data-testid="size-custom-open"
              onClick={() => onOpenSizeCustom?.(value)}
              disabled={fieldDisabled}
              className="btn"
              style={{
                padding: "6px 10px",
                fontSize: 11,
                opacity: fieldDisabled ? 0.5 : 1,
                cursor: fieldDisabled ? "not-allowed" : "pointer",
              }}
            >
              {hasCustomValue ? "Edit custom…" : "Custom…"}
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  if (field.control === "number") {
    const cap = capabilities?.[field.k];
    const max = typeof cap === "number" ? cap : field.max ?? null;
    return (
      <div data-field={field.k}>
        <NumberPresets
          label={field.label}
          hint={field.hint || null}
          presets={field.presets || [1, 2, 4, 8]}
          max={max}
          value={typeof value === "number" ? value : 1}
          onChange={onChange}
          fieldDisabled={fieldDisabled}
          disabledReason={disabledReason}
        />
      </div>
    );
  }

  if (field.control === "toggle") {
    return (
      <div data-field={field.k}>
        <Toggle
          label={field.label}
          hint={field.hint || null}
          value={!!value}
          onChange={onChange}
          fieldDisabled={fieldDisabled}
          disabledReason={disabledReason}
        />
      </div>
    );
  }

  // Other field controls without their own wrapper get an inert one for
  // e2e selectors. Catch-all to keep the contract uniform.
  return <div data-field={field.k} />;
}

function SchemaParamsPanel({
  plan,
  params,
  setParam,
  capabilities,
  onOpenSizeCustom,
}) {
  if (!plan.primary.length && !plan.advanced.length) {
    return (
      <div
        className="mono"
        style={{ fontSize: 11, color: "var(--ink-3)", padding: 12 }}
      >
        This model has no configurable parameters.
      </div>
    );
  }
  return (
    <>
      {plan.primary.map((p, idx) => {
        const pk = paramKey(p.field);
        return (
          <Fragment key={p.field.k}>
            <FieldRenderer
              plan={p}
              value={params[pk]}
              onChange={(v) => setParam(pk, v)}
              capabilities={capabilities}
              onOpenSizeCustom={onOpenSizeCustom}
            />
            {idx < plan.primary.length - 1 ? (
              <div className="hair" style={{ margin: "18px 0" }} />
            ) : null}
          </Fragment>
        );
      })}
      {plan.advanced.length > 0 ? (
        <>
          <div className="hair" style={{ margin: "18px 0" }} />
          <details>
            <summary
              className="mono caps"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                cursor: "pointer",
                outline: "none",
                userSelect: "none",
              }}
            >
              ◢ Advanced
            </summary>
            <div
              style={{
                marginTop: 12,
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              {plan.advanced.map((p) => {
                const pk = paramKey(p.field);
                return (
                  <FieldRenderer
                    key={p.field.k}
                    plan={p}
                    value={params[pk]}
                    onChange={(v) => setParam(pk, v)}
                    capabilities={capabilities}
                    onOpenSizeCustom={onOpenSizeCustom}
                  />
                );
              })}
            </div>
          </details>
        </>
      ) : null}
    </>
  );
}

function RefThumb({ file }) {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    if (!file) return undefined;
    const url = URL.createObjectURL(file);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  if (!src) return null;
  return (
    <img
      src={src}
      alt={file.name}
      style={{ width: "100%", height: "100%", objectFit: "cover" }}
    />
  );
}

function unavailableReasonCopy(reason) {
  switch (reason) {
    case "no_provider_for_tier":
      return "Not available on your tier.";
    case "no_capable_provider":
      return "No provider can run this model right now.";
    case "all_providers_circuit_open":
      return "All providers are temporarily offline. Try again in a few minutes.";
    default:
      return "Currently unavailable.";
  }
}
