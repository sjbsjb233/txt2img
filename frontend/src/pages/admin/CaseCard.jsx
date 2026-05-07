import { getApiBase } from "../../api/client.js";

// bytes_url comes back as a path (``/api/admin/...``) so the runner
// doesn't have to know its public hostname. <img src> doesn't see the
// SPA's apiFetch base, so prepend it here. Already-absolute URLs pass
// through untouched.
function absoluteImageUrl(url) {
  if (!url) return url;
  if (/^https?:\/\//.test(url)) return url;
  return getApiBase().replace(/\/+$/, "") + url;
}

function Chip({ status, judgeLevel }) {
  const labels = {
    running: "RUNNING",
    pass: "通过",
    manual_pending: "待人工",
    fail: "未通过",
    skipped: "已跳过",
  };
  const tone = {
    running: { bg: "var(--paper-2)", fg: "var(--ink-2)" },
    pass: { bg: "var(--ok)", fg: "var(--paper)" },
    manual_pending: { bg: "var(--banana)", fg: "var(--ink)" },
    fail: { bg: "var(--bad)", fg: "var(--paper)" },
    skipped: { bg: "var(--paper-2)", fg: "var(--ink-3)" },
  }[status] || { bg: "var(--paper-2)", fg: "var(--ink-2)" };

  return (
    <span
      className="mono caps"
      style={{
        background: tone.bg,
        color: tone.fg,
        padding: "2px 8px",
        fontSize: 9,
        fontWeight: 700,
        letterSpacing: "0.1em",
        border: "1px solid var(--ink)",
      }}
    >
      {labels[status] || "—"}
      {judgeLevel ? ` · ${judgeLevel}` : ""}
    </span>
  );
}

export default function CaseCard({ caseState, onLightbox, onManual }) {
  const {
    case_id,
    title,
    suite,
    params,
    judge_level,
    status,
    images = [],
    auto_verdict = [],
    error_kind,
    error_message,
    latency_ms,
    manual_prompt,
    cost_image,
    expect_error,
  } = caseState;

  return (
    <div
      className="case-card"
      data-test={`case-card-${case_id}`}
      data-status={status}
      style={{ padding: "12px 14px", marginBottom: 10 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
        }}
      >
        <span className="mono" style={{ fontWeight: 700 }}>
          {case_id}
        </span>
        <span style={{ fontSize: 13 }}>{title}</span>
        <Chip status={status} judgeLevel={judge_level} />
        <span
          className="mono"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            marginLeft: "auto",
            display: "flex",
            gap: 8,
          }}
        >
          {expect_error && <span>D · 0¥</span>}
          {!expect_error && cost_image && <span>1 image</span>}
          {latency_ms != null && <span>{latency_ms.toFixed(0)}ms</span>}
        </span>
      </div>

      {Object.keys(params || {}).length > 0 && (
        <details style={{ marginTop: 6 }}>
          <summary
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              cursor: "pointer",
              letterSpacing: "0.12em",
            }}
          >
            入参 ({Object.keys(params).length})
          </summary>
          <pre
            style={{
              marginTop: 6,
              padding: 8,
              background: "var(--paper-2)",
              border: "1px solid var(--ink-4)",
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              maxHeight: 220,
              overflow: "auto",
            }}
          >
            {JSON.stringify(params, null, 2)}
          </pre>
        </details>
      )}

      {images.length > 0 && (
        <div className="thumb-row" style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
          {images.map((img) => (
            <button
              key={`${case_id}-${img.idx}`}
              type="button"
              className="thumb"
              data-test={`thumb-${case_id}-${img.idx}`}
              onClick={() => onLightbox(img)}
              style={{
                width: 96,
                height: 96,
                border: "1px solid var(--ink)",
                cursor: "pointer",
                background: "var(--paper-2)",
                position: "relative",
                padding: 0,
              }}
            >
              <img
                src={absoluteImageUrl(img.bytes_url)}
                alt={`${case_id} #${img.idx}`}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
              />
              <span
                className="mono"
                style={{
                  position: "absolute",
                  bottom: 0,
                  left: 0,
                  right: 0,
                  background: "rgba(0,0,0,0.7)",
                  color: "var(--paper)",
                  fontSize: 9,
                  padding: "2px 4px",
                  textAlign: "left",
                }}
              >
                {img.width}×{img.height}
              </span>
            </button>
          ))}
        </div>
      )}

      <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
        {auto_verdict.map((v, i) => (
          <div
            key={i}
            className="judge-row"
            style={{
              display: "flex",
              gap: 6,
              alignItems: "baseline",
              fontSize: 12,
              color: v.pass ? "var(--ink)" : "var(--bad)",
            }}
          >
            <span
              className="mono"
              style={{
                color: v.pass ? "var(--ok)" : "var(--bad)",
                fontWeight: 700,
                width: 14,
              }}
            >
              {v.pass ? "✓" : "✗"}
            </span>
            <span>{v.text}</span>
          </div>
        ))}
        {error_kind && (
          <div
            className="judge-row"
            style={{
              display: "flex",
              gap: 6,
              alignItems: "baseline",
              fontSize: 12,
              color: "var(--bad)",
            }}
          >
            <span className="mono" style={{ color: "var(--bad)", fontWeight: 700, width: 14 }}>
              !
            </span>
            <span>
              <b>{error_kind}</b>: {error_message}
            </span>
          </div>
        )}
      </div>

      {status === "manual_pending" && manual_prompt && (
        <div
          data-test={`manual-${case_id}`}
          style={{
            marginTop: 10,
            padding: "8px 12px",
            background: "var(--paper-2)",
            borderLeft: "3px solid var(--banana)",
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.12em" }}
          >
            人工判定
          </div>
          <p style={{ margin: "6px 0 8px", fontSize: 12, lineHeight: 1.45 }}>{manual_prompt}</p>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className="btn sm"
              data-test={`manual-pass-${case_id}`}
              onClick={() => onManual("pass")}
              style={{ background: "var(--ok)", color: "var(--paper)", borderColor: "var(--ok)" }}
            >
              通过
            </button>
            <button
              type="button"
              className="btn sm"
              data-test={`manual-fail-${case_id}`}
              onClick={() => onManual("fail")}
              style={{ background: "var(--bad)", color: "var(--paper)", borderColor: "var(--bad)" }}
            >
              不通过
            </button>
            <button
              type="button"
              className="btn sm"
              data-test={`manual-skip-${case_id}`}
              onClick={() => onManual("skip")}
            >
              跳过
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
