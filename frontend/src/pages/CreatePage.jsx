import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../components/Icon.jsx";
import TopBar from "../components/TopBar.jsx";
import TurnstileModal from "../components/TurnstileModal.jsx";
import { getModels } from "../api/models.js";
import { createJob, precheck as precheckJob } from "../api/jobs.js";
import { createSession } from "../api/sessions.js";
import * as sseStore from "../store/sse.js";
import * as archiveStore from "../store/archive.js";
import { usePreferences } from "../store/preferences.js";

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

function valueAllowed(caps, key, value) {
  if (value === null || value === undefined || value === false) return true;
  const allowed = caps?.[key];
  if (Array.isArray(allowed)) {
    if (typeof value === "string") return allowed.includes(value);
  }
  if (typeof allowed === "number") {
    if (typeof value === "number") return value <= allowed;
  }
  if (typeof allowed === "boolean") {
    if (typeof value === "boolean") return value === false || allowed === true;
  }
  // No opinion at this layer → allowed.
  if (allowed === undefined || allowed === null) {
    // For boolean toggles a missing capability means "user must not opt in".
    if (typeof value === "boolean") return value === false;
    // For string fields we drop unconditionally.
    if (typeof value === "string") return false;
    // For numbers, leaving them at their default is fine.
    return true;
  }
  return true;
}

function reconcileParams(params, caps) {
  const next = { ...params };
  // String fields — drop if not in the capability list.
  ["size", "aspect_ratio", "image_size", "quality", "output_format", "background", "moderation", "thinking_level"].forEach(
    (key) => {
      if (next[key] !== undefined && !valueAllowed(caps, key, next[key])) {
        next[key] = undefined;
      }
    }
  );
  // Booleans — scrub when the cap flips from true to false.
  ["include_thoughts", "google_search", "image_search", "stream"].forEach(
    (key) => {
      if (next[key] === true && caps?.[key] !== true) next[key] = false;
    }
  );
  // n must be ≤ n_max.
  if (typeof caps?.n_max === "number" && typeof next.n === "number") {
    if (next.n > caps.n_max) next.n = caps.n_max;
  }
  return next;
}

function applyDefaults(defaults, params, caps) {
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
  return reconcileParams(next, caps);
}

// ---------------------------------------------------------------------------
// CreatePage
// ---------------------------------------------------------------------------

const N_PRESETS = [1, 2, 4, 6, 8, 12];

// Static descriptor copy for the IMAGE SIZE section (Gemini values).
// Matches the original mockup: short tag under each value chip.
const IMAGE_SIZE_NOTES = {
  "512": "preview",
  "1K": "balanced",
  "2K": "print",
  "4K": "max",
};

export default function CreatePage() {
  const navigate = useNavigate();
  const { prefs: userPrefs } = usePreferences();
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

  // Submission state.
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [showTurnstile, setShowTurnstile] = useState(false);
  const [turnstileSiteKey, setTurnstileSiteKey] = useState(null);
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
        const preferredId = initialPrefsRef.current?.model_id;
        const preferred = preferredId
          ? all.find((m) => m.model_id === preferredId && m.available)
          : null;
        const firstOk = all.find((m) => m.available) || all[0] || null;
        setSelectedModel(preferred || firstOk);
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
      applyDefaults(selectedModel.defaults, prev, selectedModel.capabilities)
    );
  }, [selectedModel?.model_id]); // eslint-disable-line react-hooks/exhaustive-deps

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
    [buildPayload, navigate, refs, selectedModel]
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
          // FE-04: feed the response straight into the archive store so the
        // page we navigate to renders the QUEUED card immediately.
        // The store also persists it to IndexedDB so a reload survives.
        await archiveStore.insertOptimistic(response);
          clientRequestIdRef.current = null;
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
    [navigate, refs, submit]
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
  // -----------------------------------------------------------------

  const caps = selectedModel?.capabilities || {};
  const maxN =
    typeof caps.n_max === "number" ? caps.n_max : 1;
  const showN = maxN > 1;
  const aspectOptions = Array.isArray(caps.aspect_ratio) ? caps.aspect_ratio : null;
  const sizeOptions = Array.isArray(caps.size) ? caps.size : null;
  const imageSizeOptions = Array.isArray(caps.image_size) ? caps.image_size : null;
  const qualityOptions = Array.isArray(caps.quality) ? caps.quality : null;
  const outputFormatOptions = Array.isArray(caps.output_format) ? caps.output_format : null;
  const backgroundOptions = Array.isArray(caps.background) ? caps.background : null;
  const moderationOptions = Array.isArray(caps.moderation) ? caps.moderation : null;
  const thinkingOptions = Array.isArray(caps.thinking_level) ? caps.thinking_level : null;
  const includeThoughtsAvail = caps.include_thoughts === true;
  const googleSearchAvail = caps.google_search === true;
  const imageSearchAvail = caps.image_search === true;
  const promptCharCap =
    typeof caps.max_prompt_chars === "number" ? caps.max_prompt_chars : 32_000;

  const setParam = (key, value) =>
    setParams((prev) => ({ ...prev, [key]: value }));

  const sessions = catalog?.sessions || [];
  const isEmpty = refs.length === 0;
  const generateCount = useMemo(() => {
    if (!showN) return 1;
    return params.n || 1;
  }, [showN, params.n]);

  const errorMessage = submitError
    ? submitError.message || "Generation failed."
    : null;

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
        subtitle="Text prompt, up to 14 reference images, any provider. Drafts autosave every keystroke."
        right={
          <>
            <button
              className="btn sm ghost"
              onClick={() => {
                setPrompt("");
                clearRefs();
                clientRequestIdRef.current = null;
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
            {/* Output count — always shown. Standard preset list mirrors
                the original mockup; chips above ``n_max`` are rendered
                disabled instead of hidden so the row width is stable
                across model switches. */}
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 8 }}
            >
              Output count
            </div>
            <div style={{ border: "1px solid var(--ink)", padding: 12, background: "#fffdf7" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                }}
              >
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  per generation
                </span>
                <div
                  className="ticker"
                  style={{ fontSize: 24, fontWeight: 900, letterSpacing: "-0.03em" }}
                >
                  ×{generateCount}
                </div>
              </div>
              <div style={{ display: "flex", gap: 4, marginTop: 8 }}>
                {N_PRESETS.map((n) => {
                  const allowed = n <= maxN;
                  const on = generateCount === n;
                  return (
                    <button
                      key={n}
                      onClick={() => allowed && setParam("n", n)}
                      disabled={!allowed}
                      title={
                        allowed
                          ? undefined
                          : `${selectedModel?.display_name || "model"} caps n at ${maxN}`
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

            <div className="hair" style={{ margin: "18px 0" }} />

            {/* Shape (aspect ratio) — 3-column tall cells, matches the
                original mockup exactly. Each cell carries a proportional
                preview and the ratio label below. */}
            {aspectOptions ? (
              <>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: 10,
                  }}
                >
                  <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                    Shape
                  </div>
                  <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                    aspect ratio
                  </div>
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                    gap: 6,
                  }}
                >
                  {aspectOptions.map((r) => {
                    const on = (params.aspect_ratio || null) === r;
                    const { w, h } = aspectBoxSize(r);
                    return (
                      <button
                        key={r}
                        onClick={() => setParam("aspect_ratio", r)}
                        style={{
                          padding: "10px 8px",
                          background: on ? "var(--ink)" : "#fffdf7",
                          border: "1px solid var(--ink)",
                          cursor: "pointer",
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
                          {r}
                        </div>
                      </button>
                    );
                  })}
                </div>
                <div className="hair" style={{ margin: "18px 0" }} />
              </>
            ) : null}

            {/* Size (gpt-image-2 only) — 2-column because labels like
                ``1024x1024`` are too long to fit 4 across in a 360px panel. */}
            {sizeOptions ? (
              <ChipGroup
                label="Size"
                hint="output dimensions"
                options={sizeOptions}
                value={params.size || null}
                onChange={(v) => setParam("size", v)}
              />
            ) : null}

            {/* Image size (gemini) — 4-column, big numeric + descriptor. */}
            {imageSizeOptions ? (
              <>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: 10,
                  }}
                >
                  <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                    Image size
                  </div>
                  <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                    longest edge
                  </div>
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
                    gap: 6,
                  }}
                >
                  {imageSizeOptions.map((s) => {
                    const on = (params.image_size || null) === s;
                    return (
                      <button
                        key={s}
                        onClick={() => setParam("image_size", s)}
                        style={{
                          padding: "10px 6px",
                          background: on ? "var(--ink)" : "#fffdf7",
                          border: "1px solid var(--ink)",
                          color: on ? "var(--banana)" : "var(--ink)",
                          cursor: "pointer",
                          textAlign: "center",
                        }}
                      >
                        <div
                          className="ticker"
                          style={{
                            fontSize: 20,
                            fontWeight: 900,
                            letterSpacing: "-0.03em",
                            lineHeight: 1,
                          }}
                        >
                          {s}
                        </div>
                        <div
                          className="mono"
                          style={{
                            fontSize: 9,
                            marginTop: 4,
                            opacity: on ? 0.8 : 0.6,
                          }}
                        >
                          {IMAGE_SIZE_NOTES[s] || ""}
                        </div>
                      </button>
                    );
                  })}
                </div>
                <div className="hair" style={{ margin: "18px 0" }} />
              </>
            ) : null}

            {/* Advanced — collapsed by default to match the original
                mockup. Holds every secondary knob: provider-specific
                Quality / Output format / Background / Moderation, plus
                Gemini's Thinking level and search-grounding toggles. We
                only render the section if at least one knob has a
                capability surface; otherwise it would appear as an
                empty disclosure. */}
            {(qualityOptions ||
              outputFormatOptions ||
              backgroundOptions ||
              moderationOptions ||
              thinkingOptions ||
              includeThoughtsAvail ||
              googleSearchAvail ||
              imageSearchAvail) ? (
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
                  {thinkingOptions ? (
                    <ChipGroup
                      label="Thinking level"
                      hint="latency vs. care"
                      options={thinkingOptions}
                      value={params.thinking_level || null}
                      onChange={(v) => setParam("thinking_level", v)}
                    />
                  ) : null}
                  {qualityOptions ? (
                    <ChipGroup
                      label="Quality"
                      hint="render fidelity"
                      options={qualityOptions}
                      value={params.quality || null}
                      onChange={(v) => setParam("quality", v)}
                    />
                  ) : null}
                  {outputFormatOptions ? (
                    <ChipGroup
                      label="Output format"
                      hint="encoded as"
                      options={outputFormatOptions}
                      value={params.output_format || null}
                      onChange={(v) => setParam("output_format", v)}
                    />
                  ) : null}
                  {backgroundOptions ? (
                    <ChipGroup
                      label="Background"
                      hint=""
                      options={backgroundOptions}
                      value={params.background || null}
                      onChange={(v) => setParam("background", v)}
                    />
                  ) : null}
                  {moderationOptions ? (
                    <ChipGroup
                      label="Moderation"
                      hint="content filter"
                      options={moderationOptions}
                      value={params.moderation || null}
                      onChange={(v) => setParam("moderation", v)}
                    />
                  ) : null}
                  {includeThoughtsAvail ? (
                    <Toggle
                      label="Include thoughts"
                      hint="surface intermediate reasoning"
                      value={!!params.include_thoughts}
                      onChange={(v) => setParam("include_thoughts", v)}
                    />
                  ) : null}
                  {googleSearchAvail ? (
                    <Toggle
                      label="Google search grounding"
                      hint="ground on web facts"
                      value={!!params.google_search}
                      onChange={(v) => setParam("google_search", v)}
                    />
                  ) : null}
                  {imageSearchAvail ? (
                    <Toggle
                      label="Image search grounding"
                      hint="use search images as context"
                      value={!!params.image_search}
                      onChange={(v) => setParam("image_search", v)}
                    />
                  ) : null}
                </div>
              </details>
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
  if (renderOption) {
    cols = Math.min(options.length, 4);
  } else if (longest >= 8) {
    cols = 2;
  } else if (longest >= 5) {
    cols = 3;
  } else {
    cols = Math.min(options.length, 4);
  }
  return (
    <div style={{ marginTop: smallTopMargin ? 4 : 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
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
          return (
            <button
              key={opt}
              onClick={() => onChange(opt)}
              style={{
                padding: "10px 6px",
                background: on ? "var(--ink)" : "#fffdf7",
                border: "1px solid var(--ink)",
                color: on ? "var(--banana)" : "var(--ink)",
                cursor: "pointer",
                textAlign: "center",
                minWidth: 0,
                overflow: "hidden",
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
      <div className="hair" style={{ margin: "18px 0" }} />
    </div>
  );
}

function Toggle({ label, hint, value, onChange }) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        background: "#fffdf7",
        border: "1px solid var(--ink)",
        padding: "8px 10px",
        cursor: "pointer",
      }}
    >
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{label}</div>
        {hint ? (
          <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            {hint}
          </div>
        ) : null}
      </div>
    </label>
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
