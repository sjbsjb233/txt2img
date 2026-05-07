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

// Map the emoji prefix on each line to a styled callout block. Per
// design v2 §5.4 the manual_prompt is structured by section markers:
//   📋 inputs · ✅ pass conditions · ❌ fail conditions
//   💡 tolerated artifacts · ℹ informational notes
// Anything else falls back to neutral.
const EMOJI_STYLE = {
  "📋": { fg: "var(--ink-2)", bg: "var(--paper-2)", border: "var(--ink-4)" },
  "✅": { fg: "var(--ok)", bg: "#e8f0e2", border: "var(--ok)" },
  "❌": { fg: "var(--bad)", bg: "#f3d6d0", border: "var(--bad)" },
  "💡": { fg: "var(--ink-2)", bg: "#fbe9a1", border: "var(--banana-deep)" },
  "ℹ": { fg: "var(--ink-2)", bg: "#dfe6f0", border: "#3a5a78" },
};

function parsePromptBlocks(prompt) {
  if (!prompt) return [];
  const lines = prompt.split("\n");
  const blocks = [];
  let current = null;
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const head = Object.keys(EMOJI_STYLE).find((e) => line.trimStart().startsWith(e));
    if (head) {
      if (current) blocks.push(current);
      current = { emoji: head, lines: [line.trimStart().slice(head.length).trim()] };
    } else if (current) {
      // Keep blank lines as soft separators inside a block but trim
      // leading whitespace so indentation collapses cleanly.
      current.lines.push(line === "" ? "" : line.replace(/^\s+/, "  "));
    }
  }
  if (current) blocks.push(current);
  return blocks;
}

function PromptBlocks({ prompt }) {
  const blocks = parsePromptBlocks(prompt);
  if (!blocks.length) {
    return (
      <p style={{ margin: "6px 0 0", fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
        {prompt}
      </p>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
      {blocks.map((b, i) => {
        const style = EMOJI_STYLE[b.emoji];
        return (
          <div
            key={i}
            style={{
              background: style.bg,
              borderLeft: `3px solid ${style.border}`,
              color: style.fg,
              padding: "6px 10px",
              fontSize: 12,
              lineHeight: 1.55,
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 2 }}>
              <span style={{ marginRight: 6 }}>{b.emoji}</span>
              {b.lines[0]}
            </div>
            {b.lines.slice(1).map((l, j) => (
              <div key={j} style={{ whiteSpace: "pre-wrap" }}>
                {l || " "}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function InputThumbnails({ inputs, onLightbox }) {
  if (!inputs || !inputs.length) return null;
  return (
    <div
      data-test="case-inputs"
      style={{
        display: "flex",
        gap: 8,
        marginTop: 6,
        marginBottom: 8,
        flexWrap: "wrap",
        padding: "6px 8px",
        background: "var(--paper-2)",
        border: "1px dashed var(--ink-4)",
      }}
    >
      <div
        className="mono caps"
        style={{
          fontSize: 9,
          color: "var(--ink-3)",
          letterSpacing: "0.12em",
          alignSelf: "center",
          marginRight: 4,
        }}
      >
        入参
      </div>
      {inputs.map((inp, i) => (
        <button
          key={i}
          type="button"
          className="thumb"
          data-test={`input-thumb-${i}`}
          onClick={() => onLightbox({ ...inp, width: 0, height: 0 })}
          style={{
            width: 64,
            height: 64,
            border: "1px solid var(--ink)",
            cursor: "pointer",
            background: "var(--paper)",
            position: "relative",
            padding: 0,
          }}
          title={inp.label}
        >
          <img
            src={absoluteImageUrl(inp.bytes_url)}
            alt={inp.label}
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
              fontSize: 8,
              padding: "1px 3px",
              textAlign: "center",
            }}
          >
            {inp.label}
          </span>
        </button>
      ))}
    </div>
  );
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
    inputs = [],
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
            padding: "10px 12px",
            background: "var(--paper)",
            border: "1px solid var(--banana-deep)",
            borderLeft: "4px solid var(--banana)",
          }}
        >
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.12em",
              marginBottom: 4,
            }}
          >
            人工判定
          </div>

          <InputThumbnails inputs={inputs} onLightbox={onLightbox} />
          <PromptBlocks prompt={manual_prompt} />

          <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
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
