// Right panel during the post-success compare view. Verdict surface
// per §3.4 of the v2 design.
//
// Sections, top to bottom:
//   1. result header — derived seq + parent + render time
//   2. spill banner — only when preservation_change_pct > 5%
//   3. diff metrics — preserve change / edit change / ratio + heatmap toggle
//   4. preservation strategy — mask-only vs full replace
//   5. actions — re-generate / discard / accept
//   6. versions — sibling list

import { Seg, Toggle } from "./RightPanel/atoms.jsx";

const SAFE_PRESERVE_PCT = 5;
const ALARM_PRESERVE_PCT = 15;

export default function CompareRightPanel({
  result,
  parent,
  derivedVersions,
  metrics,
  tier,
  strategy,
  autoStrategy,
  onStrategyChange,
  showHeatmap,
  onToggleHeatmap,
  onAccept,
  onDiscard,
  onRegenerate,
  onVersionPick,
  accepting,
  analyzing,
  maskPainted = true,
}) {
  const renderSec = result?.timing?.render_seconds;
  const isOverride = !!strategy && autoStrategy && strategy !== autoStrategy;

  // Tier text fallback when metrics haven't computed yet.
  const t = tier || "ok";
  return (
    <div className="me-right-panel" data-testid="me-compare-right">
      <div className="me-cmp-header">
        <div className="me-cmp-header__caption">result</div>
        <div className="me-cmp-header__title">#{result?.seq_no ?? "—"}</div>
        <div className="me-cmp-header__sub">
          derived from #{parent?.seq_no ?? "—"}
          {renderSec ? ` · ${renderSec.toFixed(1)}s` : ""}
        </div>
      </div>
      <div className="me-cmp-body">
        {t !== "ok" && metrics && (
          <SpillBanner tier={t} pct={metrics.preserve_change_pct} />
        )}
        <Section title="diff metrics">
          {analyzing && !metrics && (
            <div style={{ fontSize: 11, color: "var(--ink-3)" }}>analyzing…</div>
          )}
          {metrics && (
            <DiffMetrics
              metrics={metrics}
              tier={t}
              showHeatmap={showHeatmap}
              onToggleHeatmap={onToggleHeatmap}
            />
          )}
        </Section>
        <Section title="preservation strategy">
          <StrategyControl
            strategy={strategy || autoStrategy || (maskPainted ? "mask" : "full")}
            auto={autoStrategy || (maskPainted ? "mask" : "full")}
            isOverride={isOverride}
            tier={t}
            onChange={onStrategyChange}
            maskPainted={maskPainted}
          />
        </Section>
        <Section title="actions">
          <Actions
            onAccept={onAccept}
            onDiscard={onDiscard}
            onRegenerate={onRegenerate}
            accepting={accepting}
          />
        </Section>
        <Section title="versions">
          <Versions
            currentId={result?.hash_id}
            parentId={parent?.seq_no}
            list={derivedVersions || []}
            onPick={onVersionPick}
          />
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="me-cmp-section">
      <div className="me-cmp-section__head">
        <span className="me-cmp-section__title">{title}</span>
      </div>
      <div className="me-cmp-section__body">{children}</div>
    </div>
  );
}

function SpillBanner({ tier, pct }) {
  const isDanger = tier === "danger";
  const text = isDanger
    ? `model heavily modified the preserve region (~${pct}%). consider re-generating.`
    : `model also changed ~${pct}% of the preserve region. mask-only is safer.`;
  return (
    <div
      className={`me-cmp-spill me-cmp-spill--${isDanger ? "danger" : "warn"}`}
      data-testid="me-cmp-spill"
    >
      <div className="me-cmp-spill__stripe" />
      <div className="me-cmp-spill__body">
        <span style={{ fontWeight: 700, fontSize: 12, flexShrink: 0 }}>⚠</span>
        <span>{text}</span>
      </div>
    </div>
  );
}

function DiffMetrics({ metrics, showHeatmap, onToggleHeatmap }) {
  const rows = [
    {
      label: "preserve change",
      value: `${metrics.preserve_change_pct}%`,
      testid: "me-diff-preserve-change",
      status:
        metrics.preserve_change_pct >= ALARM_PRESERVE_PCT
          ? "bad"
          : metrics.preserve_change_pct >= SAFE_PRESERVE_PCT
          ? "warn"
          : "ok",
    },
    {
      label: "edit change",
      value: `${metrics.edit_change_pct}%`,
      testid: "me-diff-edit-change",
      status: metrics.edit_change_pct > 8 ? "ok" : "warn",
    },
    {
      label: "ratio",
      value: `${metrics.ratio}×`,
      testid: "me-diff-ratio",
      status: metrics.ratio > 3 ? "ok" : metrics.ratio >= 1 ? "warn" : "bad",
    },
  ];
  return (
    <div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {rows.map((r) => (
          <div className="me-cmp-metric" key={r.label} data-testid={r.testid}>
            <span className="me-cmp-metric__label">{r.label}</span>
            <span className="me-cmp-metric__value">
              <span className="me-cmp-metric__num" data-testid={`${r.testid}-value`}>{r.value}</span>
              <span className={`me-cmp-status me-cmp-status--${r.status}`}>
                {r.status === "ok" ? "✓" : r.status === "warn" ? "⚠" : "✗"}
              </span>
            </span>
          </div>
        ))}
      </div>
      <div style={{ borderTop: "1px solid var(--rule)", marginTop: 12, paddingTop: 4 }}>
        <Toggle
          on={!!showHeatmap}
          label="show heatmap overlay"
          onChange={onToggleHeatmap}
        />
      </div>
    </div>
  );
}

function StrategyControl({ strategy, auto, isOverride, tier, onChange, maskPainted = true }) {
  const hint = !maskPainted
    ? "no mask painted · only full replace makes sense"
    : isOverride
    ? `manual override · auto would pick ${auto === "mask" ? "mask-only" : "full replace"}`
    : tier === "danger"
    ? "auto-picked: model changed too much for a clean composite"
    : "auto-picked: model stayed within the mask region";
  // ``Seg`` doesn't expose per-option disable, so when there's no mask
  // we render only the "full replace" choice.
  const options = maskPainted
    ? [
        { value: "mask", label: "mask-only" },
        { value: "full", label: "full replace" },
      ]
    : [{ value: "full", label: "full replace" }];
  return (
    <div data-testid="me-cmp-strategy" data-value={strategy} data-auto={auto} data-override={isOverride ? "true" : "false"}>
      <Seg
        options={options}
        value={strategy}
        onChange={onChange}
        dense
      />
      <div
        className={`me-cmp-strategy-hint ${
          isOverride ? "me-cmp-strategy-hint--override" : ""
        }`}
        data-testid="me-cmp-strategy-hint"
      >
        {hint}
      </div>
    </div>
  );
}

function Actions({ onAccept, onDiscard, onRegenerate, accepting }) {
  return (
    <div className="me-cmp-actions">
      <ActionBtn
        icon="↻"
        label="re-generate (same params)"
        onClick={onRegenerate}
        testid="me-cmp-regenerate"
      />
      <ActionBtn
        icon="✗"
        label="discard changes"
        onClick={onDiscard}
        testid="me-cmp-discard"
      />
      <ActionBtn
        icon="✓"
        label={accepting ? "accepting…" : "accept changes"}
        onClick={onAccept}
        primary
        disabled={accepting}
        testid="me-cmp-accept"
      />
    </div>
  );
}

function ActionBtn({ icon, label, primary, onClick, disabled, testid }) {
  return (
    <button
      type="button"
      className={`btn${primary ? " primary shadowed" : ""}`}
      style={{
        height: 36,
        justifyContent: "flex-start",
        gap: 10,
        padding: "0 14px",
        fontSize: 12,
        fontWeight: 600,
      }}
      onClick={onClick}
      disabled={disabled}
      data-testid={testid}
    >
      <span
        className="mono"
        style={{
          fontSize: 13,
          fontWeight: 700,
          width: 14,
          display: "inline-flex",
          justifyContent: "center",
        }}
      >
        {icon}
      </span>
      <span>{label}</span>
    </button>
  );
}

function Versions({ currentId, parentId, list, onPick }) {
  if (!list.length) {
    return (
      <div style={{ padding: 6, color: "var(--ink-3)", fontSize: 11 }}>
        versions of #{parentId ?? "—"} · 0 derived
      </div>
    );
  }
  return (
    <div>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--ink-3)",
          marginBottom: 4,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}
      >
        versions of #{parentId ?? "—"} · {list.length} derived
      </div>
      {list.map((v) => {
        const active = v.hash_id === currentId;
        return (
          <div
            key={v.hash_id}
            className="me-cmp-version-row"
            onClick={() => !active && onPick?.(v)}
          >
            <div className="me-cmp-version-row__thumb" />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="me-cmp-version-row__seq">#{v.seq_no}</div>
              <div className="me-cmp-version-row__label">
                {v.derivation_kind || "edit"}
              </div>
            </div>
            {active && <span className="me-cmp-version-row__now">now</span>}
          </div>
        );
      })}
    </div>
  );
}
