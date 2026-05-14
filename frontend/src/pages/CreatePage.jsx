import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../components/Icon.jsx";
import TopBar from "../components/TopBar.jsx";
import TurnstileModal from "../components/TurnstileModal.jsx";
import SizeCustomModal, { isValidSize } from "../components/SizeCustomModal.jsx";
import DraftToast from "../components/DraftToast.jsx";
import OutputCountSlider from "../components/OutputCountSlider.jsx";
import { getModels } from "../api/models.js";
import { createJob, precheck as precheckJob } from "../api/jobs.js";
import { createSession } from "../api/sessions.js";
import * as sseStore from "../store/sse.js";
import * as archiveStore from "../store/archive.js";
import { usePreferences } from "../store/preferences.js";
import { useAuth } from "../store/auth.js";
import { useDraftAutosave } from "../hooks/useDraftAutosave.js";
import { useStickyState } from "../hooks/useStickyState.js";

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

// 10-char alphanumeric id with a prefix — same shape BatchPage uses for
// its slot/set ids so backend `nano()` consumers don't have to special-case
// per-page.
function nanoId(prefix) {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < 10; i += 1) {
    out += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return `${prefix}_${out}`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

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
      // chip list when ``capabilities.size_allow_custom`` is true — but
      // only if the value still parses as a 5-rule-compliant
      // ``WIDTHxHEIGHT``. Without that gate, a stale ``auto`` (when the
      // admin removed it from caps) or any non-string would survive
      // model swaps and trip the backend's INVALID_PARAMETER guard.
      const isCustomSizeKeep =
        field.k === "size" &&
        capabilities?.size_allow_custom === true &&
        typeof cur === "string" &&
        /^\d+x\d+$/.test(cur) &&
        isValidSize(...cur.split("x").map(Number)).ok;
      if (
        cur != null &&
        !isCustomSizeKeep &&
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
  // Snapshot the user's preferred defaults once on mount so a change
  // in /settings while this page is open doesn't yank the active
  // model — or the seeded aspect / batch size — from under the user.
  //
  // ``aspect_ratio`` and ``batch_size`` are still seeded into params
  // for any *first-time* visit to a model (no sticky entry yet). Once
  // the user has saved a sticky snapshot for that model, the snapshot
  // wins. This mirrors the pre-sticky behaviour so a power user with
  // ``ratio=3:2, batch=2`` doesn't have to re-pick on every brand-new
  // model they try.
  const initialPrefsRef = useRef(null);
  if (initialPrefsRef.current === null) {
    initialPrefsRef.current = {
      model_id: userPrefs?.generation?.default_model_id || null,
      aspect_ratio: userPrefs?.generation?.default_aspect_ratio || null,
      batch_size: userPrefs?.generation?.default_batch_size || 1,
    };
  }

  // Catalog state (from /api/models). `null` while we wait for the first load.
  const [catalog, setCatalog] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [selectedModel, setSelectedModel] = useState(null);

  // Form state. Params seeding is handled by the model-resolution
  // chain (sticky → user pref defaults → model.defaults) once the
  // catalog lands; until then params is empty and the right rail
  // renders no values, which is fine because the model picker is
  // empty too.
  const [prompt, setPrompt] = useState("");
  const [params, setParams] = useState({});
  const [refs, setRefs] = useState([]); // array of File objects (insertion order)
  const [sessionId, setSessionId] = useState(null);

  // Per-model expansion state for the right-rail "◢ Advanced" block.
  // ``null`` is "not decided yet" — the model-change effect below
  // resolves it from sticky (or false) once the active model lands,
  // and the hook ignores the null so a render-before-resolution
  // doesn't wipe an existing sticky entry.
  const [advancedOpen, setAdvancedOpen] = useState(null);

  // Sticky layer — long-lived per-user model preference + per-model
  // params + per-model advanced-open. Lives across page reloads,
  // navigation, and Generate success. Independent of the (ephemeral)
  // Draft layer below.
  const {
    hydratedSticky,
    flushSticky,
    getParamsFor,
    getAdvancedOpenFor,
  } = useStickyState({
    userId,
    selectedModelId: selectedModel?.model_id || null,
    paramsForCurrentModel: params,
    advancedOpenForCurrentModel: advancedOpen,
  });

  // Draft restore lands prompt / refs / session_id into the form
  // before any user interaction. Model + params come from sticky
  // independently and are not part of the restore payload.
  const handleRestoreDraft = useCallback((restored) => {
    if (typeof restored.prompt === "string") setPrompt(restored.prompt);
    if (Array.isArray(restored.refs)) setRefs(restored.refs);
    if (typeof restored.sessionId === "string" || restored.sessionId === null) {
      setSessionId(restored.sessionId);
    }
  }, []);

  // Submission state.
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [showTurnstile, setShowTurnstile] = useState(false);
  const [turnstileSiteKey, setTurnstileSiteKey] = useState(null);
  const [showSizeCustom, setShowSizeCustom] = useState(false);
  // Fan-out progress for models where the upstream returns 1 image per
  // call and the user picked n>1. ``total === 0`` means "not fanning
  // out" — single-shot submit hides the progress indicator entirely.
  const [submitProgress, setSubmitProgress] = useState({ done: 0, total: 0 });
  const pendingSubmitRef = useRef(null); // payload waiting for cf token
  // When a fan-out worker trips the backend captcha gate mid-flight, the
  // pool pauses and opens the Turnstile modal. The resolver below is the
  // bridge back into ``submitFanout``: ``onCaptchaSuccess`` calls it with
  // the freshly minted token, ``TurnstileModal``'s onClose rejects it.
  // Single-shot ``submitSingle`` keeps using ``pendingSubmitRef`` and is
  // unaffected.
  const fanoutCaptchaResolverRef = useRef(null);
  const lastFetchAtRef = useRef(0);
  const clientRequestIdRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getModels();
      setCatalog(data);
      lastFetchAtRef.current = Date.now();
      setLoadError("");
      // Mount + refresh model resolution chain (design doc §4.6):
      //   1. sticky.last_model_id (cross-reload "remember my pick")
      //   2. user preferences default_model_id
      //   3. first available model
      //   4. catalog[0] as a final tiebreaker
      // Re-evaluated on every refresh so a model going offline mid-
      // session lands the user on a working pick. (Cross-tab sync is
      // out of scope per design doc §8.4 — sticky is hydrated once.)
      const all = data?.models || [];
      const stillThere = all.find(
        (m) => m.model_id === (selectedModel?.model_id || "")
      );
      if (!stillThere) {
        // Fallback path swaps the active model out from under the
        // user. If they had unsaved param edits inside the sticky
        // debounce window for the prior model, flush them now —
        // otherwise the new model's defaults effect clobbers them
        // (mirrors the explicit-click flow in the model picker).
        flushSticky();
        const stickyId = hydratedSticky?.last_model_id || null;
        const fromSticky = stickyId
          ? all.find((m) => m.model_id === stickyId && m.available)
          : null;
        const preferredId = initialPrefsRef.current?.model_id;
        const preferred = preferredId
          ? all.find((m) => m.model_id === preferredId && m.available)
          : null;
        const firstOk = all.find((m) => m.available) || all[0] || null;
        // Reset advancedOpen synchronously so the next render doesn't
        // briefly show the prior model's open state — the model-change
        // effect picks up the right value on the same commit.
        setAdvancedOpen(null);
        setSelectedModel(fromSticky || preferred || firstOk);
      } else {
        setSelectedModel(stillThere);
      }
    } catch (err) {
      setLoadError(err?.message || "Failed to load models.");
    }
  }, [selectedModel?.model_id, hydratedSticky, flushSticky]);

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

  // Whenever the active model changes, replace right-rail params.
  // Resolution order (design doc §4.4 + first-time-seed compat):
  //   1. sticky.params_by_model[model] — the user's saved snapshot
  //   2. user-preference defaults (aspect_ratio + batch_size) when
  //      no sticky snapshot exists for this model. Preserves the
  //      pre-sticky behaviour so a power user with prefs set still
  //      sees them on a brand-new model.
  //   3. model.defaults — fills any field neither layer above set.
  // ``applyDefaults`` reconciles against the new schema either way:
  // drops keys that aren't in ``ui_schema`` and clamps numerics.
  useEffect(() => {
    if (!selectedModel) return;
    const stickyParams = getParamsFor(selectedModel.model_id);
    let baseParams;
    if (stickyParams) {
      baseParams = stickyParams;
    } else {
      const seed = {};
      const initial = initialPrefsRef.current;
      if (initial?.aspect_ratio) seed.aspect_ratio = initial.aspect_ratio;
      if (initial?.batch_size && initial.batch_size > 1) seed.n = initial.batch_size;
      baseParams = seed;
    }
    setParams(
      applyDefaults(
        selectedModel.defaults,
        baseParams,
        selectedModel.capabilities,
        selectedModel.ui_schema
      )
    );
    // Hydrate the Advanced block's expansion state for this model.
    // Falls back to ``false`` (collapsed) when nothing is saved yet —
    // matches the previous uncontrolled <details> default.
    const stickyAdvanced = getAdvancedOpenFor(selectedModel.model_id);
    setAdvancedOpen(typeof stickyAdvanced === "boolean" ? stickyAdvanced : false);
  }, [selectedModel?.model_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Draft autosave + restore — only the ephemeral trio (prompt / refs /
  // session). Model + params persistence lives in the Sticky layer
  // above so it survives Generate-success and Clear.
  const { toast: draftToast, clearDraft } = useDraftAutosave({
    userId,
    prompt,
    refs,
    sessionId,
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

  // Submit one job that the upstream provider can satisfy in a single
  // POST. The existing single-shot path; unchanged behaviour for models
  // where ``n_max_upstream`` matches (or exceeds) the picked ``n``.
  const submitSingle = useCallback(
    async (captchaToken, overrideN = null) => {
      if (!selectedModel) return;
      const payload = buildPayload();
      if (!payload) return;
      if (typeof overrideN === "number") {
        // Defense-in-depth clamp from ``submit()``: we get the clamped
        // value here because ``setParams`` is async — the closure inside
        // ``buildPayload`` still sees the pre-clamp ``params.n``. Apply
        // the override on the freshly built payload before it's sent so
        // the wire value matches what we told the user we were doing.
        payload.n = overrideN;
      }
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

  // Submit N independent ``n=1`` jobs that share one ``set_id``. Used
  // when the user picked ``n > n_max_upstream`` on a model whose
  // upstream returns a single image per call (Gemini today). The shape
  // of the resulting data is identical to a Batch slot with
  // ``image_count = N`` except that ``batch_id`` stays null.
  //
  // Worker-pool concurrency mirrors BatchPage's fan-out path so we
  // reuse the same SSE-bus pacing and partial-submit semantics.
  const submitFanout = useCallback(
    async (captchaToken, targetN) => {
      if (!selectedModel) return;
      const basePayload = buildPayload();
      if (!basePayload) return;
      // We hand-allocate per-call ids below; reset the page-level one so
      // a follow-up single-shot submit doesn't reuse a fan-out slot id.
      clientRequestIdRef.current = null;

      const setId = nanoId("set");
      const baseReqId = nanoId("req");
      const tasks = Array.from({ length: targetN }, (_, i) => ({
        ordinal: i + 1,
        client_request_id: `${baseReqId}_${i + 1}`,
      }));

      // Turnstile tokens are single-use: putting the same one on every
      // sub-POST would let the backend accept the 1st and reject the
      // 2nd-Nth as "token already consumed". We park the token in a
      // ref so exactly one worker can claim it — the first one to
      // reach the consumer wins, the rest go through without one. The
      // captcha is required because the user crossed a rolling
      // anti-abuse threshold; one successful submission resets that
      // counter for the next short window, so a single redemption
      // covers the burst in practice. If the backend signals
      // CAPTCHA_REQUIRED again mid-flight on a later worker we don't
      // try to re-prompt (the user already saw one modal this round);
      // we just count it as a partial-submit failure and surface the
      // error if every sub-POST loses.
      const captchaTokenRef = { current: captchaToken || null };

      setSubmitting(true);
      setSubmitError(null);
      setSubmitProgress({ done: 0, total: targetN });

      const concurrencyMax = catalog?.meta?.batch_concurrency_max || 4;
      const concurrency = Math.max(1, Math.min(concurrencyMax, targetN));
      const queue = [...tasks];
      let done = 0;
      let succeeded = 0;
      let firstError = null;
      // Pool-wide pause flag. Set when the first worker trips a
      // CAPTCHA_REQUIRED 412; cleared after the Turnstile modal hands
      // back a token. Other workers spin in a short sleep-loop while
      // paused so they don't claim a task and then immediately stall
      // inside ``createJob``.
      const pausedRef = { current: false };
      // Latch: only one worker mounts the Turnstile modal; the rest
      // re-enqueue their task and wait for the resume signal. Without
      // this, all N concurrent 412s would try to open N modals.
      const captchaInFlightRef = { current: false };
      // Promise-based bridge to ``onCaptchaSuccess``. ``cancelled``
      // turns true when the user closes the modal — workers see this
      // and bail without re-enqueueing anything.
      let cancelled = false;

      const tryOpenCaptcha = async (firstErr) => {
        // Issue a fresh precheck so the modal renders against the
        // current site key — keeps the flow correct if the operator
        // rotated keys mid-session.
        let siteKeyForModal = null;
        try {
          const fresh = await precheckJob({ model: selectedModel.model_id });
          if (!fresh?.captcha_required) {
            // Race: backend's counter relaxed between the 412 and the
            // precheck. Treat as "no captcha needed", let workers
            // resume with the next iteration's natural retry path.
            return null;
          }
          siteKeyForModal = fresh.site_key || null;
        } catch {
          // Precheck failure isn't fatal — fall back to whatever site
          // key the page already has.
        }
        if (siteKeyForModal) setTurnstileSiteKey(siteKeyForModal);
        return new Promise((resolve, reject) => {
          fanoutCaptchaResolverRef.current = { resolve, reject };
          setShowTurnstile(true);
        });
      };

      const work = async () => {
        while (queue.length || pausedRef.current) {
          // Spin while paused so we don't claim a task and then sit
          // inside ``createJob`` with a token that hasn't arrived yet.
          // 50 ms is short enough to feel snappy, long enough not to
          // burn CPU.
          while (pausedRef.current) {
            await sleep(50);
            if (cancelled) return;
          }
          if (cancelled) return;
          const t = queue.shift();
          if (!t) break;
          const subPayload = {
            ...basePayload,
            n: 1,
            set_id: setId,
            client_request_id: t.client_request_id,
          };
          if (captchaTokenRef.current) {
            subPayload.captcha_token = captchaTokenRef.current;
            captchaTokenRef.current = null;
          }
          try {
            const response = await createJob({
              payload: subPayload,
              references: refs,
            });
            await archiveStore.insertOptimistic(response);
            succeeded += 1;
          } catch (err) {
            if (err?.code === "CAPTCHA_REQUIRED") {
              // Push the failed task back onto the queue head so a
              // post-captcha worker can retry it. Without this we'd
              // silently drop one image per 412 even after the modal
              // succeeds.
              queue.unshift(t);
              if (!captchaInFlightRef.current) {
                captchaInFlightRef.current = true;
                pausedRef.current = true;
                try {
                  const token = await tryOpenCaptcha(err);
                  // ``tryOpenCaptcha`` returns null when precheck
                  // says no captcha is needed — workers resume with
                  // no token and let the next 412 (if any) re-open.
                  captchaTokenRef.current = token || null;
                } catch (cancelErr) {
                  // User closed the modal — drain the queue and let
                  // partial-submit banner pick up the slack below.
                  cancelled = true;
                  queue.length = 0;
                  if (!firstError) firstError = cancelErr;
                } finally {
                  captchaInFlightRef.current = false;
                  pausedRef.current = false;
                }
              }
              // ``done`` does NOT tick: this task is going to be
              // re-attempted by some worker, success/fail will count
              // there.
              continue;
            }
            if (!firstError) firstError = err;
            // eslint-disable-next-line no-console
            console.warn("create fan-out job failed", err);
          }
          done += 1;
          setSubmitProgress({ done, total: targetN });
          // SSE bus pacing — same stagger BatchPage uses.
          await sleep(20);
        }
      };

      try {
        await Promise.all(
          Array.from({ length: concurrency }, () => work())
        );
      } finally {
        // Whichever path we leave on (success / partial / cancel),
        // wipe the captcha resolver so a stale modal close can't poke
        // a finished pool. Defensive — ``setShowTurnstile(false)`` in
        // the success path already detaches the modal.
        fanoutCaptchaResolverRef.current = null;
        setSubmitting(false);
      }

      setSubmitProgress({ done: 0, total: 0 });

      if (succeeded === 0) {
        if (firstError) setSubmitError(firstError);
        return;
      }
      // Partial submit — some children landed, some didn't. The user
      // gave us a single intent ("Generate ×N") and we delivered K<N.
      // Stay on the Create page so the user actually sees the gap;
      // navigating to /archive would unmount the banner before they
      // can read it. The survivors are already queued and will show
      // up when they navigate over themselves.
      if (succeeded < targetN) {
        const dropped = targetN - succeeded;
        const code = firstError?.code;
        const reason =
          code === "CAPTCHA_REQUIRED"
            ? "anti-abuse limit reached — wait ~60 s before retrying"
            : code === "CAPTCHA_CANCELLED"
            ? "verification cancelled"
            : firstError?.message || "rejected";
        const banner = new Error(
          `Submitted ${succeeded} of ${targetN}. ${dropped} dropped: ${reason}. Survivors are in the Archive.`
        );
        banner.code = "PARTIAL_SUBMIT";
        setSubmitError(banner);
        return;
      }
      clearDraft({ silent: true });
      navigate("/archive");
    },
    [
      buildPayload,
      catalog?.meta?.batch_concurrency_max,
      clearDraft,
      navigate,
      refs,
      selectedModel,
    ]
  );

  // Route to single vs fan-out based on capabilities. When
  // ``n_max_upstream`` is missing (older capability rows), it falls back
  // to ``n_max`` and the comparison is identical to v1 behaviour, so
  // existing models are unaffected.
  const submit = useCallback(
    async (captchaToken) => {
      if (!selectedModel) return;
      const caps = selectedModel.capabilities || {};
      const capN = typeof caps.n_max === "number" ? caps.n_max : 1;
      const upstream =
        typeof caps.n_max_upstream === "number"
          ? caps.n_max_upstream
          : capN;
      // Defense-in-depth: clamp the picked count to ``[1, n_max]`` here
      // too. The new free-input slider already clamps inside its own
      // onChange, but a stale ``params.n`` (sticky leftover from a model
      // with a higher cap, or any future control that bypasses the
      // slider) must never reach buildPayload — otherwise the user sees
      // ``×N`` in the UI while the backend silently caps to ``n_max``.
      const rawRequested = typeof params.n === "number" ? params.n : 1;
      const requested = Math.max(1, Math.min(rawRequested, capN));
      if (requested !== rawRequested) {
        // Sync params so the next render and any subsequent submit
        // settle on the clamped value.
        setParams((prev) => ({ ...prev, n: requested }));
      }
      if (requested > upstream && requested > 1) {
        await submitFanout(captchaToken, requested);
      } else {
        await submitSingle(captchaToken, requested);
      }
    },
    [selectedModel, params.n, submitSingle, submitFanout]
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
      // Three paths land here:
      //   1. Fan-out mid-flight 412 → resolver bridges the token back
      //      into the worker pool so it can resume.
      //   2. Initial precheck said captcha → no payload was built yet.
      //   3. createJob threw CAPTCHA_REQUIRED mid-flight on the single
      //      path → payload was stashed in pendingSubmitRef.
      const fanoutResolver = fanoutCaptchaResolverRef.current;
      if (fanoutResolver) {
        fanoutCaptchaResolverRef.current = null;
        fanoutResolver.resolve(token);
        return;
      }
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
  // Upstream per-call ceiling — falls back to ``n_max`` when older
  // capability rows haven't been migrated yet (keeps v1 behaviour for
  // legacy providers).
  const nMaxUpstream =
    typeof caps.n_max_upstream === "number"
      ? caps.n_max_upstream
      : maxN;
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

  // The Create page fans out into n=1 sub-requests whenever the user
  // picks n > n_max_upstream. We surface a small hint near the slider
  // so the user knows the multi-image request is N parallel calls, not
  // one upstream call.
  const willFanOut = generateCount > nMaxUpstream && generateCount > 1;

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
                // Clear button — ephemeral trio only. Sticky (model +
                // per-model params) is intentionally preserved here;
                // see design doc §4.2.
                setPrompt("");
                clearRefs();
                setSessionId(null);
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
                    data-testid={`create-model-tile-${m.model_id}`}
                    data-model-id={m.model_id}
                    data-available={m.available}
                    onClick={() => {
                      if (disabled) return;
                      // Flush any pending sticky write for the *current*
                      // model before swapping — otherwise the in-flight
                      // debounce gets clobbered by the new model's
                      // params reset (design doc §4.4 / §8.1).
                      flushSticky();
                      // Reset advancedOpen alongside the model swap so
                      // the next render doesn't briefly show the prior
                      // model's open state before the model-change
                      // effect resolves the new value from sticky.
                      setAdvancedOpen(null);
                      setSelectedModel(m);
                    }}
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
              advancedOpen={advancedOpen === true}
              onToggleAdvanced={(next) => setAdvancedOpen(!!next)}
            />
            {willFanOut ? (
              <div
                data-testid="create-fanout-hint"
                className="mono"
                style={{
                  marginTop: 10,
                  fontSize: 10,
                  lineHeight: 1.4,
                  color: "var(--ink-3)",
                  background: "#fffdf7",
                  border: "1px dashed var(--ink-3)",
                  padding: "6px 8px",
                }}
              >
                This model returns 1 image per upstream call. We&apos;ll fan out into {generateCount} parallel background requests.
              </div>
            ) : null}
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
            {submitProgress.total > 0 ? (
              <div
                data-testid="create-submit-progress"
                className="mono"
                style={{
                  marginBottom: 10,
                  fontSize: 11,
                  color: "var(--ink-2)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>Submitting…</span>
                  <span data-testid="create-submit-progress-text">
                    {submitProgress.done}/{submitProgress.total}
                  </span>
                </div>
                <div
                  style={{
                    height: 4,
                    background: "var(--paper-3)",
                    border: "1px solid var(--ink)",
                    position: "relative",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      right: `${
                        100 -
                        Math.min(
                          100,
                          Math.round(
                            (submitProgress.done / submitProgress.total) * 100
                          )
                        )
                      }%`,
                      background: "var(--banana)",
                    }}
                  />
                </div>
              </div>
            ) : null}
            <button
              data-testid="create-generate-button"
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
                ? submitProgress.total > 0
                  ? `Submitting ${submitProgress.done}/${submitProgress.total}…`
                  : "Submitting…"
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
          // Fan-out path: a closed modal is the user saying "stop";
          // unblock the workers by rejecting so they can see the
          // partial-submit banner instead of spinning forever.
          const fanoutResolver = fanoutCaptchaResolverRef.current;
          if (fanoutResolver) {
            fanoutCaptchaResolverRef.current = null;
            const err = new Error("Captcha verification cancelled.");
            err.code = "CAPTCHA_CANCELLED";
            fanoutResolver.reject(err);
          }
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
// Schema-driven param panel — FieldRenderer / SchemaParamsPanel.
//
// The output-count control lives in components/OutputCountSlider.jsx now;
// FieldRenderer pulls it in for ``field.control === "number"``.
// ---------------------------------------------------------------------------

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
        <OutputCountSlider
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
  advancedOpen,
  onToggleAdvanced,
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
          <details
            data-testid="advanced-details"
            open={!!advancedOpen}
            onToggle={(e) => onToggleAdvanced?.(e.currentTarget.open)}
          >
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
