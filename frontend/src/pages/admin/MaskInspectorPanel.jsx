import { useEffect, useMemo, useState } from "react";
import { getApiBase } from "../../api/client.js";
import { postManualVerdict } from "../../api/admin/providerTestSuite.js";

function absoluteImageUrl(url) {
  if (!url) return url;
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  return getApiBase().replace(/\/+$/, "") + url;
}

// Mask 测试方案 v2 §5.1/§5.2 thresholds, mirrored on the client so the
// inspector panel can show "value / threshold" rows without re-asking
// the server. Keep in sync with provider_test_runner._judge_mask_*.
const INPAINT_METRICS = [
  { key: "preserve_score", label: "preserve_score", op: "<", threshold: 0.05, format: (v) => v.toFixed(3) },
  { key: "edit_score", label: "edit_score", op: ">", threshold: 0.08, format: (v) => v.toFixed(3) },
  { key: "ratio_score", label: "ratio_score", op: ">", threshold: 3.0, format: (v) => v.toFixed(1) },
];

const OUTPAINT_METRICS = [
  { key: "preserve_score", label: "preserve_score", op: "<", threshold: 0.05, format: (v) => v.toFixed(3) },
  { key: "black_void_pct", label: "black_void_pct", op: "<", threshold: 5.0, format: (v) => v.toFixed(1) + "%" },
  { key: "extension_edges_pct", label: "extension_edges_pct", op: ">", threshold: 1.0, format: (v) => v.toFixed(2) + "%" },
];

function metricRow(metrics, def) {
  const v = metrics[def.key];
  if (v == null) return { ...def, value: null, pass: false };
  const pass = def.op === "<" ? v < def.threshold : v > def.threshold;
  return { ...def, value: v, pass };
}

function ThumbButton({ label, src, onClick, accent = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: 140,
        height: 100,
        border: `1px solid ${accent ? "var(--banana-deep)" : "var(--ink)"}`,
        cursor: "pointer",
        background: "var(--paper-2)",
        position: "relative",
        padding: 0,
      }}
      title={label}
    >
      {src ? (
        <img
          src={src}
          alt={label}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : (
        <div
          className="mono"
          style={{
            width: "100%",
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 10,
            color: "var(--ink-3)",
          }}
        >
          （未生成）
        </div>
      )}
      <span
        className="mono"
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          background: "rgba(0,0,0,0.72)",
          color: "var(--paper)",
          fontSize: 9,
          padding: "2px 4px",
          textAlign: "center",
          letterSpacing: "0.06em",
        }}
      >
        {label}
      </span>
    </button>
  );
}

export default function MaskInspectorPanel({ caseState, providerId, runId, onClose, onLightbox, onManual }) {
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const {
    case_id,
    title,
    status,
    ok,
    auto_verdict = [],
    inputs = [],
    images = [],
    diagnostics = [],
    mask_metrics: metrics = null,
    manual_prompt,
    params = {},
  } = caseState;

  const kind = case_id && /^M[1-4]$/.test(case_id) ? "inpaint" : "outpaint";
  const defs = kind === "inpaint" ? INPAINT_METRICS : OUTPAINT_METRICS;
  const rows = useMemo(
    () => (metrics ? defs.map((d) => metricRow(metrics, d)) : []),
    [metrics, defs],
  );

  // Group the artifacts: input scene / canvas, mask overlay (from diagnostics),
  // returned image, diff heatmap.
  const refInputs = inputs.filter((i) => i.kind === "ref");
  const overlay = diagnostics.find((d) => d.kind === "overlay");
  const heatmap = diagnostics.find((d) => d.kind === "heatmap");
  const out = images[0];

  const verdictTone =
    status === "pass"
      ? { bg: "var(--ok)", fg: "var(--paper)", label: "通过" }
      : status === "fail"
        ? { bg: "var(--bad)", fg: "var(--paper)", label: "未通过" }
        : status === "manual_pending"
          ? { bg: "var(--banana)", fg: "var(--ink)", label: "待人工" }
          : status === "skipped"
            ? { bg: "var(--paper-2)", fg: "var(--ink-3)", label: "已跳过" }
            : { bg: "var(--paper-2)", fg: "var(--ink-2)", label: status || "—" };

  const failingVerdicts = auto_verdict.filter((v) => !v.pass);
  const reasonLine = failingVerdicts.length
    ? failingVerdicts.map((v) => v.text).join("; ")
    : ok
      ? "三项指标均通过"
      : "未生成任何返回图";

  const sendManual = async (verdict) => {
    if (busy) return;
    setBusy(true);
    try {
      if (runId && providerId) {
        await postManualVerdict(providerId, runId, case_id, verdict).catch(() => {
          /* tolerate offline — UI already updates */
        });
      }
      onManual?.(verdict);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-test="mask-inspector"
      data-case-id={case_id}
      role="dialog"
      aria-label={`Mask inspector ${case_id}`}
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(640px, 100vw)",
        background: "var(--paper)",
        borderLeft: "2px solid var(--ink)",
        boxShadow: "-5px 0 0 var(--ink)",
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "14px 18px",
          borderBottom: "1px solid var(--ink)",
          background: "var(--paper-2)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
            审核详情 · {case_id}
          </div>
          <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            {title}
          </div>
        </div>
        <button
          type="button"
          className="btn sm"
          onClick={onClose}
          data-test="mask-inspector-close"
        >
          关闭
        </button>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "12px 18px 18px" }}>
        <div
          data-test="mask-inspector-verdict"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "8px 12px",
            border: "1px solid var(--ink)",
            background: "var(--paper-2)",
          }}
        >
          <span
            className="mono caps"
            style={{
              background: verdictTone.bg,
              color: verdictTone.fg,
              padding: "2px 10px",
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.12em",
              border: "1px solid var(--ink)",
            }}
          >
            自动判定 · {verdictTone.label}
          </span>
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
            原因: {reasonLine}
          </span>
        </div>

        {/* 入参概览 */}
        <div style={{ marginTop: 12 }}>
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.16em",
              marginBottom: 4,
            }}
          >
            入参概览
          </div>
          <pre
            data-test="mask-inspector-params"
            style={{
              padding: 8,
              background: "var(--paper-2)",
              border: "1px solid var(--ink-4)",
              fontSize: 11,
              maxHeight: 160,
              overflow: "auto",
            }}
          >
            {JSON.stringify({ prompt: params?.prompt, size: params?.size, quality: params?.quality, n: params?.n, method: /^M[1357]$/.test(case_id) ? "native" : "fallback" }, null, 2)}
          </pre>
        </div>

        {/* 中间过程·横向四联图 */}
        <div style={{ marginTop: 14 }}>
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.16em",
              marginBottom: 6,
            }}
          >
            中间过程
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {refInputs.map((inp, i) => (
              <ThumbButton
                key={`ref-${i}`}
                label={i === 0 ? "原图 / canvas" : inp.label}
                src={absoluteImageUrl(inp.bytes_url)}
                onClick={() =>
                  onLightbox?.({ ...inp, width: 0, height: 0 })
                }
              />
            ))}
            {overlay && (
              <ThumbButton
                label="掩膜叠加"
                src={absoluteImageUrl(overlay.bytes_url)}
                onClick={() =>
                  onLightbox?.({
                    bytes_url: overlay.bytes_url,
                    name: overlay.name,
                    mime: overlay.mime,
                    byte_size: overlay.byte_size,
                    width: 0,
                    height: 0,
                  })
                }
                accent
              />
            )}
            {out ? (
              <ThumbButton
                label="返回图"
                src={absoluteImageUrl(out.bytes_url)}
                onClick={() => onLightbox?.(out)}
              />
            ) : (
              <ThumbButton label="返回图" src={null} onClick={() => {}} />
            )}
            {heatmap && (
              <ThumbButton
                label="差分热力图"
                src={absoluteImageUrl(heatmap.bytes_url)}
                onClick={() =>
                  onLightbox?.({
                    bytes_url: heatmap.bytes_url,
                    name: heatmap.name,
                    mime: heatmap.mime,
                    byte_size: heatmap.byte_size,
                    width: 0,
                    height: 0,
                  })
                }
                accent
              />
            )}
          </div>
        </div>

        {/* 分区指标 */}
        <div style={{ marginTop: 14 }}>
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.16em",
              marginBottom: 6,
            }}
          >
            分区指标
          </div>
          <table
            data-test="mask-inspector-metrics"
            className="mono"
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 11,
              border: "1px solid var(--ink)",
            }}
          >
            <thead>
              <tr style={{ background: "var(--paper-2)" }}>
                <th style={{ padding: 6, textAlign: "left", borderBottom: "1px solid var(--ink-4)" }}>指标</th>
                <th style={{ padding: 6, textAlign: "right", borderBottom: "1px solid var(--ink-4)" }}>当前值</th>
                <th style={{ padding: 6, textAlign: "right", borderBottom: "1px solid var(--ink-4)" }}>阈值</th>
                <th style={{ padding: 6, textAlign: "center", borderBottom: "1px solid var(--ink-4)" }}>结果</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ padding: 10, textAlign: "center", color: "var(--ink-3)" }}>
                    （未计算指标 — 返回图缺失或判定失败）
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.key}
                  data-test={`metric-row-${r.key}`}
                  data-pass={r.pass ? "1" : "0"}
                  style={{ background: r.pass ? "transparent" : "rgba(155,45,32,0.10)" }}
                >
                  <td style={{ padding: 6 }}>{r.label}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>
                    {r.value == null ? "—" : r.format(r.value)}
                  </td>
                  <td style={{ padding: 6, textAlign: "right" }}>
                    {r.op} {r.format(r.threshold)}
                  </td>
                  <td style={{ padding: 6, textAlign: "center", color: r.pass ? "var(--ok)" : "var(--bad)", fontWeight: 700 }}>
                    {r.pass ? "✓" : "✗"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 审核条款 */}
        {manual_prompt && (
          <div style={{ marginTop: 14 }}>
            <div
              className="mono caps"
              style={{
                fontSize: 9,
                color: "var(--ink-3)",
                letterSpacing: "0.16em",
                marginBottom: 6,
              }}
            >
              审核条款 · 人工复核用
            </div>
            <pre
              data-test="mask-inspector-prompt"
              style={{
                padding: 10,
                background: "var(--paper-2)",
                border: "1px solid var(--ink-4)",
                fontSize: 11,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                maxHeight: 260,
                overflow: "auto",
              }}
            >
              {manual_prompt}
            </pre>
          </div>
        )}

        {/* 人工覆盖判定 */}
        <div style={{ marginTop: 14 }}>
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.16em",
              marginBottom: 6,
            }}
          >
            人工覆盖判定
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn sm"
              data-test="mask-manual-pass"
              disabled={busy}
              onClick={() => sendManual("pass")}
              style={{ background: "var(--ok)", color: "var(--paper)", borderColor: "var(--ok)" }}
            >
              推翻为通过
            </button>
            <button
              type="button"
              className="btn sm"
              data-test="mask-manual-fail"
              disabled={busy}
              onClick={() => sendManual("fail")}
              style={{ background: "var(--bad)", color: "var(--paper)", borderColor: "var(--bad)" }}
            >
              维持不通过
            </button>
            <button
              type="button"
              className="btn sm"
              data-test="mask-manual-skip"
              disabled={busy}
              onClick={() => sendManual("skip")}
            >
              跳过
            </button>
          </div>
          <div className="mono" style={{ marginTop: 6, fontSize: 10, color: "var(--ink-3)" }}>
            会走 apply_manual_verdict 并记 audit 日志。
          </div>
        </div>
      </div>
    </div>
  );
}
