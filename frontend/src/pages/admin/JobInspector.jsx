// Inline accordion shown under one row of the RECENT JOBS table in
// UserDetailDrawer. Renders the user-visible JobDetail surface plus the
// admin-only diagnostics returned by /api/admin/jobs/<hash>/inspect.
//
// The component is deliberately self-contained: it owns the fetch +
// loading + error state for one job. Multiple inspectors may be open
// simultaneously inside one drawer; each one fetches independently.

import { useEffect, useState } from "react";
import AuthorizedImage from "../../components/AuthorizedImage.jsx";
import {
  downloadImageFile,
  imageOriginalUrl,
  imageThumbUrl,
  referenceThumbUrl,
} from "../../api/archive.js";
import * as adminJobs from "../../api/admin/jobs.js";

// ---------------------------------------------------------------------------
// Tiny formatters — mirror the design doc spec for chip / label rendering.
// ---------------------------------------------------------------------------

function fmtSeconds(s) {
  if (s == null || Number.isNaN(s)) return "—";
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(2)}m`;
}

function fmtMs(ms) {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function fmtTimestamp(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toISOString().replace("T", " ").replace(/\..*/, "Z");
  } catch {
    return iso;
  }
}

function fmtTimeOnly(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(11, 19);
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Layout primitives
// ---------------------------------------------------------------------------

function Divider() {
  return (
    <div
      style={{
        position: "relative",
        height: 1,
        background: "var(--ink)",
        margin: "18px 0 14px",
      }}
    >
      <span
        style={{
          position: "absolute",
          top: -8,
          left: "50%",
          transform: "translateX(-50%)",
          padding: "0 10px",
          background: "var(--paper-2)",
          color: "var(--bad)",
          letterSpacing: "0.18em",
          fontWeight: 700,
          fontSize: 10,
          fontFamily: "var(--font-mono)",
          textTransform: "uppercase",
          whiteSpace: "nowrap",
        }}
      >
        ADMIN DIAGNOSTICS · admin-only
      </span>
    </div>
  );
}

function SectionTitle({ children }) {
  return (
    <div
      className="mono caps"
      style={{
        fontSize: 10,
        color: "var(--ink-3)",
        letterSpacing: "0.16em",
        marginBottom: 8,
      }}
    >
      {children}
    </div>
  );
}

function Chip({ label, value, tone = "default" }) {
  const toneToBg = {
    default: "transparent",
    ok: "var(--ok)",
    warn: "var(--warn)",
    bad: "var(--bad)",
    soft: "var(--paper-3)",
  };
  const toneToFg = {
    default: "var(--ink)",
    ok: "var(--paper)",
    warn: "var(--ink)",
    bad: "var(--paper)",
    soft: "var(--ink)",
  };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 7px",
        border: "1px solid var(--ink)",
        background: toneToBg[tone] || "transparent",
        color: toneToFg[tone] || "var(--ink)",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.06em",
      }}
    >
      <span style={{ opacity: 0.7 }}>{label}</span>
      <span>{value}</span>
    </span>
  );
}

function MetaRow({ k, v }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "3px 0",
        borderBottom: "1px dashed var(--rule-2)",
      }}
    >
      <span style={{ color: "var(--ink-3)" }}>{k}</span>
      <span style={{ fontWeight: 600 }}>{v}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-blocks
// ---------------------------------------------------------------------------

function LifecycleStrip({ lifecycle }) {
  if (!lifecycle) return null;
  const segments = [
    { k: "queued", v: lifecycle.queued_seconds },
    { k: "admission", v: lifecycle.admission_seconds },
    { k: "routing", v: lifecycle.routing_seconds },
    { k: "attempts", v: lifecycle.attempts_seconds, hi: true },
    { k: "finalize", v: lifecycle.finalize_seconds },
  ];
  return (
    <div
      data-testid="ji-lifecycle"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, 1fr)",
        gap: 0,
        border: "1px solid var(--ink)",
      }}
    >
      {segments.map((seg, i) => (
        <div
          key={seg.k}
          style={{
            padding: "8px 10px",
            borderRight:
              i < segments.length - 1 ? "1px solid var(--ink)" : "none",
            background: seg.hi ? "var(--banana-soft)" : "var(--paper)",
            fontFamily: "var(--font-mono)",
          }}
        >
          <div
            style={{
              fontSize: 8,
              color: "var(--ink-3)",
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              marginBottom: 4,
            }}
          >
            {seg.k}
          </div>
          <div style={{ fontSize: 11, fontWeight: 700 }}>
            {fmtSeconds(seg.v)}
          </div>
        </div>
      ))}
    </div>
  );
}

function UserStateChips({ state, synthetic = false }) {
  if (!state) {
    return (
      <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
        unavailable
      </div>
    );
  }
  const failTone = state.recent_fail_rate_n > 0 ? "bad" : "default";
  const captchaTone = state.captcha_required
    ? state.captcha_verified
      ? "ok"
      : "bad"
    : "default";
  const softTone = state.soft_quota_triggered ? "warn" : "ok";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {synthetic && (
        <div
          style={{
            padding: "4px 8px",
            border: "1px dashed var(--ink-3)",
            color: "var(--ink-3)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
          }}
        >
          no dispatch-time snapshot recorded · showing current user
          values from the DB instead of historical state
        </div>
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Chip label="tier" value={state.tier} />
        <Chip
          label="quota"
          value={`${state.today_count} / ${state.hard_quota_effective}`}
        />
        <Chip
          label="soft"
          value={state.soft_quota_triggered ? "triggered" : "below"}
          tone={softTone}
        />
        <Chip
          label="captcha"
          value={
            state.captcha_required
              ? state.captcha_verified
                ? "verified"
                : "required"
              : "n/a"
          }
          tone={captchaTone}
        />
        <Chip
          label="recent fail-rate"
          value={`${state.recent_fail_rate_n} / ${state.recent_fail_rate_total}`}
          tone={failTone}
        />
      </div>
    </div>
  );
}

function RoutingPool({ routing, degraded }) {
  // The backend now synthesises a minimal trace (chosen provider only)
  // when routing.json is absent on disk, so a fully ``null`` payload is
  // rare. We still treat ``routing == null`` as "no data at all" and
  // tell the admin why.
  if (!routing) {
    return (
      <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
        no routing trace recorded — this job ran before per-job traces
        were persisted, and the historical filter / score breakdown can
        no longer be reconstructed.
      </div>
    );
  }
  const filtered = routing.filtered_out || [];
  const scored = routing.scored || [];
  // Synthetic / minimal traces have no filtered_out and a single
  // scored row whose component breakdown is empty — surface that fact
  // clearly so admins don't read meaningful scoring into a placeholder.
  const isSynthetic =
    !!degraded?.includes("routing") &&
    filtered.length === 0 &&
    scored.length <= 1 &&
    scored.every(
      (s) => !s.components || Object.keys(s.components).length === 0,
    );
  return (
    <div style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
      {isSynthetic && (
        <div
          style={{
            marginBottom: 8,
            padding: "4px 8px",
            border: "1px dashed var(--ink-3)",
            color: "var(--ink-3)",
            fontSize: 10,
          }}
        >
          historical trace not recorded · showing chosen provider only
        </div>
      )}
      <div data-testid="ji-routing-summary" style={{ color: "var(--ink-3)" }}>
        {routing.pool_total} registered ·{" "}
        <strong style={{ color: "var(--ink)" }}>
          {routing.pool_survived}
        </strong>{" "}
        survived · {routing.pool_scored} scored
      </div>
      {filtered.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              marginBottom: 4,
            }}
          >
            Filtered out
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 10px" }}>
            {filtered.map((f, idx) => (
              <span key={`${f.provider_id}-${idx}`}>
                <span style={{ fontWeight: 600 }}>{f.label}</span>
                <span style={{ color: "var(--ink-3)" }}>
                  {" "}
                  ({f.reason}
                  {f.detail ? `: ${f.detail}` : ""})
                </span>
              </span>
            ))}
          </div>
        </div>
      )}
      {scored.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              marginBottom: 4,
            }}
          >
            Scored
          </div>
          <div
            data-testid="ji-routing-scored"
            style={{
              display: "grid",
              gridTemplateColumns:
                "28px 1.4fr repeat(5, 0.6fr) 0.7fr",
              gap: 4,
              border: "1px solid var(--ink)",
              padding: "6px 8px",
              background: "var(--paper)",
            }}
          >
            <div style={{ color: "var(--ink-3)" }}>#</div>
            <div style={{ color: "var(--ink-3)" }}>provider</div>
            <div style={{ color: "var(--ink-3)" }}>cost</div>
            <div style={{ color: "var(--ink-3)" }}>succ</div>
            <div style={{ color: "var(--ink-3)" }}>lat</div>
            <div style={{ color: "var(--ink-3)" }}>load</div>
            <div style={{ color: "var(--ink-3)" }}>fresh</div>
            <div style={{ color: "var(--ink-3)" }}>total</div>
            {scored.map((s) => (
              <ScoredRow key={s.provider_id} s={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ScoredRow({ s }) {
  const cells = [
    s.chosen ? "✓" : s.rank,
    s.label,
    fmtScore(s.components?.cost),
    fmtScore(s.components?.success),
    fmtScore(s.components?.latency),
    fmtScore(s.components?.load),
    fmtScore(s.components?.freshness),
    s.total_score?.toFixed(3),
  ];
  const bg = s.chosen ? "#dceadf" : "transparent";
  return (
    <>
      {cells.map((c, i) => (
        <div
          key={`${s.provider_id}-${i}`}
          data-chosen={s.chosen ? "1" : "0"}
          style={{
            background: bg,
            padding: "2px 4px",
            fontWeight: i === 7 ? 700 : 500,
          }}
        >
          {c}
        </div>
      ))}
    </>
  );
}

function fmtScore(v) {
  if (v == null) return "—";
  return Number(v).toFixed(2);
}

// The raw upstream log endpoint requires admin auth and lives under
// the configured ``api_base``, so a plain ``<a href>`` neither attaches
// the bearer token nor lands on the right origin in environments where
// the frontend talks to a remote backend. Fetch via the shared
// apiFetch wrapper, then open the response as a blob URL in a new tab.
function RawLogLink({ hashId, attemptNo }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const open = async () => {
    if (busy || !hashId) return;
    setBusy(true);
    setErr(null);
    try {
      const payload = await adminJobs.getUpstreamLog(hashId, attemptNo);
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const w = window.open(url, "_blank", "noopener,noreferrer");
      // Browsers GC the blob URL when nothing references it; keep
      // it alive ~30s so the new tab has time to finish loading.
      setTimeout(() => URL.revokeObjectURL(url), 30_000);
      if (!w) setErr("popup blocked");
    } catch (e) {
      setErr(e.message || "load failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={open}
      disabled={busy}
      style={{
        background: "transparent",
        border: "none",
        cursor: busy ? "wait" : "pointer",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: err ? "var(--bad)" : "var(--ink-3)",
        padding: 0,
      }}
      title={err || "open raw upstream log in a new tab"}
    >
      {busy
        ? "loading…"
        : err
          ? `attempt_${attemptNo}.json (${err})`
          : `attempt_${attemptNo}.json ↗`}
    </button>
  );
}

function AttemptCard({ a, hashId }) {
  const isOk = a.ok;
  const bg = isOk ? "#dceadf" : "#fdecea";
  const badgeBg = isOk ? "var(--ok)" : "var(--bad)";
  const snap = a.provider_snapshot || {};
  return (
    <div
      data-testid="ji-attempt"
      data-ok={isOk ? "1" : "0"}
      style={{
        background: bg,
        border: "1px solid var(--ink)",
        padding: "10px 12px",
        marginTop: 8,
        fontFamily: "var(--font-mono)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 18,
            height: 18,
            background: badgeBg,
            color: "#fff",
            fontWeight: 700,
            fontSize: 11,
          }}
        >
          {a.attempt_no}
        </span>
        <span style={{ fontWeight: 700, fontSize: 12 }}>
          {a.provider_label}
        </span>
        <span
          style={{
            padding: "1px 5px",
            background: badgeBg,
            color: "#fff",
            fontSize: 9,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            fontWeight: 700,
          }}
        >
          {isOk ? "succeeded" : a.error_kind || "OTHER"}
        </span>
        {a.upstream_status != null && (
          <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
            http {a.upstream_status}
          </span>
        )}
        <div style={{ flex: 1 }} />
        <RawLogLink hashId={hashId} attemptNo={a.attempt_no} />
        {/* end raw log */}
        <span style={{ fontSize: 11, fontWeight: 600 }}>
          {fmtMs(a.latency_ms)}
        </span>
      </div>

      <div
        style={{
          marginTop: 6,
          fontSize: 10,
          color: "var(--ink-3)",
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <span>circuit · {snap.circuit_state || "—"}</span>
        <span>
          succ@5m ·{" "}
          {snap.success_rate_5m == null
            ? "—"
            : `${(snap.success_rate_5m * 100).toFixed(0)}%`}
        </span>
        <span>p50 · {fmtMs(snap.p50_latency_ms)}</span>
        <span>
          conc · {snap.current_concurrency ?? "—"}/
          {snap.max_concurrency ?? "—"}
        </span>
        <span>
          rpm · {snap.current_rpm ?? "—"}/{snap.rpm_limit ?? "—"}
        </span>
      </div>

      {a.upstream_body_excerpt && (
        <div
          style={{
            marginTop: 6,
            fontSize: 11,
            fontStyle: "italic",
            color: "var(--ink-3)",
          }}
        >
          “{a.upstream_body_excerpt}”
        </div>
      )}
    </div>
  );
}

function OutcomeBlock({ inspect }) {
  const isOk = inspect.status === "SUCCEEDED";
  const tone = isOk ? "var(--ok)" : "var(--bad)";
  const cny = (n) => `¥${Number(n || 0).toFixed(3)}`;
  return (
    <div
      data-testid="ji-outcome"
      style={{
        border: `2px solid ${tone}`,
        padding: "10px 12px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        rowGap: 4,
        columnGap: 24,
      }}
    >
      <div>
        <span style={{ color: "var(--ink-3)" }}>provider · </span>
        <span style={{ fontWeight: 600, color: isOk ? "var(--ok)" : "var(--ink)" }}>
          {inspect.provider_used || "—"}
        </span>
      </div>
      <div>
        <span style={{ color: "var(--ink-3)" }}>retries · </span>
        <span style={{ fontWeight: 600 }}>{inspect.retries}</span>
      </div>
      <div>
        <span style={{ color: "var(--ink-3)" }}>cost · </span>
        <span style={{ fontWeight: 600 }}>
          {isOk ? cny(inspect.cost_cny) : `${cny(0)} (no charge)`}
        </span>
      </div>
      <div>
        <span style={{ color: "var(--ink-3)" }}>images · </span>
        <span style={{ fontWeight: 600 }}>
          {(inspect.images || []).length}
          {(inspect.images || []).length > 0 &&
            ` (${inspect.images[0].width}×${inspect.images[0].height})`}
        </span>
      </div>
      <div>
        <span style={{ color: "var(--ink-3)" }}>finished · </span>
        <span style={{ fontWeight: 600 }}>
          {fmtTimeOnly(inspect.timing?.finished_at)}
        </span>
      </div>
      <div>
        <span style={{ color: "var(--ink-3)" }}>updated · </span>
        <span style={{ fontWeight: 600 }}>
          {fmtTimestamp(inspect.updated_at)}
        </span>
      </div>
      {!isOk && inspect.status_reason && (
        <div style={{ gridColumn: "1 / span 2", marginTop: 4 }}>
          <span style={{ color: "var(--bad)", fontWeight: 700 }}>
            {inspect.status_reason}
          </span>
        </div>
      )}
      {inspect.circuit_ripples?.length > 0 && (
        <div style={{ gridColumn: "1 / span 2" }}>
          <span style={{ color: "var(--ink-3)" }}>circuit ripples · </span>
          <span>
            {inspect.circuit_ripples.length} provider
            {inspect.circuit_ripples.length === 1 ? "" : "s"} degraded
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top-level inspector
// ---------------------------------------------------------------------------

export default function JobInspector({ hashId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [requeueMsg, setRequeueMsg] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    adminJobs
      .inspectJob(hashId)
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || "Failed to load diagnostics.");
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hashId]);

  if (loading) {
    return (
      <div
        data-testid="job-inspector"
        data-state="loading"
        style={{
          padding: 14,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--ink-3)",
        }}
      >
        loading diagnostics…
      </div>
    );
  }

  if (error || !data) {
    return (
      <div
        data-testid="job-inspector"
        data-state="error"
        style={{
          padding: 14,
          background: "#fdecea",
          border: "1px solid var(--bad)",
          color: "var(--bad)",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
        }}
      >
        failed to load diagnostics: {error || "unknown error"}
      </div>
    );
  }

  const inspect = data;
  const firstImg = inspect.images?.[0];
  const ratio =
    firstImg && firstImg.width && firstImg.height
      ? firstImg.width / firstImg.height
      : null;
  const shape = firstImg
    ? `${firstImg.width}×${firstImg.height}${
        ratio ? ` · ${ratio.toFixed(2)}` : ""
      }`
    : "—";

  const failedDefault =
    inspect.status === "FAILED" || inspect.retries > 0;

  const handleRequeue = async () => {
    setRequeueMsg("requeuing…");
    try {
      const res = await adminJobs.requeueJob(inspect.hash_id);
      setRequeueMsg(`requeued · ${res.new_hash_id}`);
    } catch (err) {
      setRequeueMsg(err.message || "requeue failed");
    }
  };

  return (
    <div
      data-testid="job-inspector"
      data-state="ready"
      data-hash={inspect.hash_id}
      style={{
        borderTop: "1px solid var(--ink)",
        borderLeft: "3px solid var(--banana)",
        background: "var(--paper-2)",
        padding: "14px 16px 16px",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 10,
        }}
      >
        <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}>
          JOB · {inspect.hash_id}
        </div>
        <div style={{ flex: 1 }} />
        <button
          className="btn sm"
          onClick={handleRequeue}
          disabled={
            !["SUCCEEDED", "FAILED", "CANCELLED"].includes(inspect.status)
          }
          data-testid="ji-requeue"
        >
          re-queue
        </button>
        <a
          href={`/archive?focus=${encodeURIComponent(inspect.hash_id)}`}
          target="_blank"
          rel="noopener noreferrer"
          className="btn sm ghost"
        >
          open in archive ↗
        </a>
        {requeueMsg && (
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            {requeueMsg}
          </span>
        )}
      </div>

      {/* ARTEFACTS · what the user sees */}
      <SectionTitle>ARTEFACTS &amp; PARAMS · what the user sees</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 14 }}>
        <div
          style={{
            width: 220,
            aspectRatio: "4/3",
            border: "1px solid var(--ink)",
            background: "var(--paper-3)",
            position: "relative",
            overflow: "hidden",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {firstImg ? (
            <AuthorizedImage
              src={imageThumbUrl(inspect.hash_id, firstImg.order)}
              alt=""
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
              }}
            />
          ) : (
            <span
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-3)" }}
            >
              no output
            </span>
          )}
          {firstImg?.starred && (
            <span
              style={{
                position: "absolute",
                top: 4,
                right: 4,
                background: "var(--banana)",
                color: "var(--ink)",
                fontSize: 11,
                fontWeight: 700,
                padding: "1px 6px",
              }}
            >
              ★
            </span>
          )}
        </div>
        <div>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            {firstImg && (
              <button
                className="btn sm"
                style={{
                  background: "var(--ink)",
                  color: "var(--banana)",
                }}
                onClick={() =>
                  downloadImageFile(
                    imageOriginalUrl(inspect.hash_id, firstImg.order),
                    `${inspect.hash_id}_${String(firstImg.order).padStart(
                      2,
                      "0",
                    )}.${firstImg.format || "bin"}`,
                  )
                }
                data-testid="ji-download"
              >
                ↓ download
              </button>
            )}
            <a
              href={`/archive?focus=${encodeURIComponent(inspect.hash_id)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn sm ghost"
            >
              open in archive ↗
            </a>
          </div>
          <div
            style={{
              marginTop: 10,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
            }}
          >
            <MetaRow k="model" v={inspect.model_display_name || inspect.model} />
            <MetaRow k="shape" v={shape} />
            <MetaRow
              k="created"
              v={fmtTimestamp(
                inspect.timing?.queued_at || inspect.updated_at,
              )}
            />
            <MetaRow
              k="queue·render"
              v={`${fmtSeconds(inspect.timing?.queue_seconds)} · ${fmtSeconds(inspect.timing?.render_seconds)}`}
            />
            <MetaRow
              k="session"
              v={inspect.session?.name || inspect.session_id || "—"}
            />
          </div>
        </div>
      </div>

      {/* PROMPT */}
      <div style={{ marginTop: 14 }}>
        <SectionTitle>PROMPT</SectionTitle>
        <div
          style={{
            padding: "10px 12px",
            background: "var(--paper)",
            border: "1px solid var(--ink)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            position: "relative",
          }}
        >
          {inspect.prompt || "—"}
          {inspect.prompt && (
            <button
              onClick={() => navigator.clipboard?.writeText(inspect.prompt)}
              style={{
                position: "absolute",
                bottom: 4,
                right: 6,
                background: "transparent",
                border: "none",
                cursor: "pointer",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "var(--ink-3)",
              }}
            >
              copy
            </button>
          )}
        </div>
      </div>

      {(inspect.references || []).length > 0 && (
        <div style={{ marginTop: 14 }}>
          <SectionTitle>
            REFERENCES · {inspect.references.length} uploaded
          </SectionTitle>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(5, 1fr)",
              gap: 5,
            }}
          >
            {inspect.references.map((r) => (
              <div
                key={r.order}
                style={{
                  aspectRatio: "1/1",
                  border: "1px solid var(--ink)",
                  background: "var(--paper-3)",
                  overflow: "hidden",
                }}
              >
                <AuthorizedImage
                  src={referenceThumbUrl(inspect.hash_id, r.order)}
                  alt={r.filename}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      <Divider />

      <div style={{ marginBottom: 14 }}>
        <SectionTitle>LIFECYCLE</SectionTitle>
        <LifecycleStrip lifecycle={inspect.lifecycle} />
      </div>

      <div style={{ marginBottom: 14 }}>
        <SectionTitle>USER STATE AT DISPATCH</SectionTitle>
        <UserStateChips
          state={inspect.user_state_at_submit}
          synthetic={
            !!inspect.degraded_sections?.includes("user_state_synthetic")
          }
        />
      </div>

      <div style={{ marginBottom: 14 }}>
        <SectionTitle>ROUTING — PROVIDER POOL</SectionTitle>
        <RoutingPool
          routing={inspect.routing}
          degraded={inspect.degraded_sections}
        />
      </div>

      <div style={{ marginBottom: 14 }}>
        <SectionTitle>ATTEMPT CHAIN</SectionTitle>
        {(inspect.attempts || []).length === 0 ? (
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-3)", fontStyle: "italic" }}
          >
            no attempts (job failed admission)
          </div>
        ) : (
          inspect.attempts.map((a) => (
            <AttemptCard
              key={a.attempt_no}
              a={a}
              hashId={inspect.hash_id}
            />
          ))
        )}
      </div>

      <div style={{ marginBottom: 14 }}>
        <SectionTitle>OUTCOME</SectionTitle>
        <OutcomeBlock inspect={inspect} />
      </div>

      {inspect.degraded_sections?.length > 0 && (
        <div
          className="mono"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            marginTop: 4,
          }}
        >
          degraded sections · {inspect.degraded_sections.join(", ")}
        </div>
      )}

      {/* RAW PARAMS & FLAGS */}
      <details
        open={failedDefault}
        style={{ marginTop: 6 }}
        data-testid="ji-raw-params"
      >
        <summary
          className="mono caps"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            cursor: "pointer",
            userSelect: "none",
            letterSpacing: "0.16em",
          }}
        >
          ◢ RAW PARAMS &amp; FLAGS
        </summary>
        <div
          style={{
            marginTop: 8,
            padding: "8px 10px",
            background: "var(--paper)",
            border: "1px solid var(--ink)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            display: "grid",
            gridTemplateColumns: "1fr",
          }}
        >
          {Object.entries({
            ...(inspect.params || {}),
            ...Object.fromEntries(
              Object.entries(inspect.flags || {}).map(([k, v]) => [
                `flag.${k}`,
                v,
              ]),
            ),
          }).map(([k, v]) => (
            <div
              key={k}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "3px 0",
                borderBottom: "1px dashed var(--rule-2)",
              }}
            >
              <span style={{ color: "var(--ink-3)" }}>{k}</span>
              <span style={{ fontWeight: 600 }}>{String(v)}</span>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
