// Centered, draggable-feeling "we're working on it" modal that owns the
// generating UX. Lives above the LockVeil and CanvasPartial; the only
// interactive surface in the layered stack is the (optional) cancel
// button — the rest is informational so the user knows the editor is
// locked, what stage we're in, and how much longer to wait.
//
// The progress bar is a fake-but-believable curve: real terminal
// events from the backend snap it to 100% in 250 ms, so the user
// experience is "watched a smooth bar climb, then it landed." See
// the curve formula in the inline comment below.

import { useEffect, useRef, useState } from "react";

const PHASES_WITH_PARTIAL = ["queue", "prefill", "sample", "partial 1", "partial 2", "final"];
const PHASES_NO_PARTIAL = ["queue", "prefill", "sample", "final"];

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

// Smooth curve that asymptotes to ~92% so the bar never visually
// "stalls at 99%." Real completion later snaps to 100% in 250 ms.
function fakeProgress(elapsedSec, avgSec) {
  if (avgSec <= 0) return 0;
  const k = avgSec * 0.5;
  return 0.92 * (1 - Math.exp(-elapsedSec / k));
}

export default function GeneratingCard({
  startedAt,
  avgSec = 24,
  phase = "running", // submitting | running | finalizing
  partials = [], // array of partial events received so far
  supportsPartial = false,
  prompt = "",
  modelId = "",
  quality = "",
  maskCoveragePct = null, // 0..100 or null
  partialThumbUrl = null, // when supportsPartial: latest partial's URL
  seqNo = null,
  onCancel = null, // optional — when provided, footer renders cancel btn
}) {
  // Tick the elapsed clock locally so the parent doesn't need to
  // re-render for each second.
  const [, force] = useState(0);
  const rafRef = useRef(null);
  useEffect(() => {
    let alive = true;
    function tick() {
      if (!alive) return;
      force((n) => (n + 1) % 1_000_000);
      rafRef.current = setTimeout(tick, 250);
    }
    tick();
    return () => {
      alive = false;
      if (rafRef.current) clearTimeout(rafRef.current);
    };
  }, []);

  const elapsedMs = Math.max(0, Date.now() - (startedAt || Date.now()));
  const elapsedSec = elapsedMs / 1000;
  const remainSec = Math.max(0, avgSec - elapsedSec);
  const overTime = elapsedSec > avgSec * 1.5;

  // Decide displayed progress.
  let pct = fakeProgress(elapsedSec, avgSec);
  // Snap to phase-specific floors when partial events have landed —
  // gives the bar a satisfying "lift" on each event.
  if (supportsPartial) {
    if (partials.length >= 1) pct = Math.max(pct, 0.45);
    if (partials.length >= 2) pct = Math.max(pct, 0.8);
  }
  // Once finalizing, snap close to full.
  if (phase === "finalizing") pct = Math.max(pct, 0.95);

  // Pick the "current phase" based on what we know.
  const phaseList = supportsPartial ? PHASES_WITH_PARTIAL : PHASES_NO_PARTIAL;
  let currentPhase = "queue";
  if (phase === "submitting") currentPhase = "queue";
  else if (phase === "finalizing") currentPhase = "final";
  else if (supportsPartial && partials.length >= 2) currentPhase = "partial 2";
  else if (supportsPartial && partials.length >= 1) currentPhase = "partial 1";
  else if (elapsedSec > 1) currentPhase = "sample";
  else currentPhase = "prefill";

  // Phase id used by tests to assert without text matching.
  const phaseSlug =
    currentPhase === "partial 1" ? "partial1" :
    currentPhase === "partial 2" ? "partial2" :
    currentPhase;
  const pctRound = Math.round(clamp(pct, 0, 1) * 100);
  return (
    <div
      className="me-gen-card"
      data-testid="me-generating-card"
      data-legacy-testid="me-gen-card"
      data-phase={phaseSlug}
      role="dialog"
      aria-label="generating"
    >
      <div className="me-gen-card__head">
        <div className="me-gen-card__head-spin" />
        <span className="me-gen-card__head-label">generating</span>
        {seqNo != null && (
          <span className="me-gen-card__head-sub">· #{seqNo} pending</span>
        )}
        <span className="me-gen-card__head-time">
          {overTime
            ? "almost there…"
            : `elapsed ${Math.round(elapsedSec)}s · ~${Math.round(remainSec)}s left`}
        </span>
      </div>
      <div className="me-gen-card__body">
        <div className="me-gen-card__thumb" data-testid="me-gen-card-thumb">
          {supportsPartial && partialThumbUrl ? (
            <>
              <img className="me-gen-card__thumb-img" src={partialThumbUrl} alt="" />
              <div className="me-gen-card__thumb-scan" />
              <div className="me-gen-card__thumb-tag">partial {Math.min(partials.length, 2)} / 2</div>
            </>
          ) : (
            <div className="me-gen-card__thumb-spin" />
          )}
        </div>
        <div className="me-gen-card__meta">
          <div>
            <div className="me-gen-card__label">prompt</div>
            <div className="me-gen-card__prompt" data-testid="me-gen-card-prompt">
              {prompt || "(no prompt)"}
            </div>
          </div>
          <div className="me-gen-card__metarow">
            <span>
              {maskCoveragePct != null
                ? `mask ${maskCoveragePct}%`
                : "mask method fallback"}
            </span>
            <span>
              model {modelId || "—"}
              {quality ? ` · ${quality}` : ""}
            </span>
          </div>
          <div style={{ position: "relative", marginTop: 8 }}>
            <div
              className="me-gen-card__progress"
              data-testid="me-generating-progress"
              data-progress={pctRound}
              data-phase={phaseSlug}
            >
              <div
                className="me-gen-card__progress-fill"
                style={{ width: `${pctRound}%` }}
              />
            </div>
            <div className="me-gen-card__progress-pct">
              {pctRound}%
            </div>
          </div>
          <div className="me-gen-card__phases" data-testid="me-generating-stage-track">
            {phaseList.map((p, i) => (
              <span
                key={p}
                className={`me-gen-card__phase ${
                  p === currentPhase ? "me-gen-card__phase--active" : ""
                }`}
              >
                {p}
                {i < phaseList.length - 1 ? " ·" : ""}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="me-gen-card__foot">
        <div className="me-gen-card__foot-text">
          {onCancel ? (
            <>
              editor is locked while generating to prevent mask / prompt drift.
              <br />
              result will replace the canvas when complete.
            </>
          ) : (
            "editor is locked while generating · leave to archive anytime"
          )}
        </div>
        {onCancel && (
          <button
            type="button"
            className="btn sm"
            style={{
              borderColor: "var(--bad)",
              color: "var(--bad)",
              fontWeight: 700,
              boxShadow: "2px 2px 0 var(--bad)",
            }}
            onClick={onCancel}
            data-testid="me-generating-cancel"
          >
            cancel
            <span
              className="kbd"
              style={{
                marginLeft: 6,
                background: "rgba(0,0,0,0.06)",
                boxShadow: "none",
                fontSize: 10,
              }}
            >
              Esc
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
