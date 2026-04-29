import Icon from "../../components/Icon.jsx";
import { Hair } from "./atoms.jsx";

const SWITCHES = [
  {
    k: "emergency.pause_generation",
    on: false,
    label: "Pause generation",
    desc: "POST /api/jobs → 403. Running jobs untouched.",
  },
  {
    k: "emergency.pause_image_access",
    on: false,
    label: "Pause image access",
    desc: "/thumb /original → 403 for non-admin.",
  },
  {
    k: "emergency.block_new_member_login",
    on: false,
    label: "Block non-admin login",
    desc: "Only admins can sign in.",
  },
  {
    k: "emergency.force_captcha_global",
    on: true,
    label: "Force global captcha",
    desc: "precheck always require_captcha.",
  },
];

const SECTIONS = [
  {
    title: "Scheduler",
    prefix: "scheduler",
    items: [
      { k: "global_max_workers", v: 32, hint: "全局并发硬上限" },
      { k: "min_share_per_lane", v: 0.05, hint: "每条 lane 至少 5% 吞吐" },
      { k: "deadline_promotion_seconds", v: 300, hint: "Free 等过即升一级 lane" },
    ],
  },
  {
    title: "Soft penalty",
    prefix: "soft_penalty",
    items: [
      { k: "base_delay_seconds", v: 5 },
      { k: "max_delay_seconds", v: 15 },
      { k: "base_fail_probability", v: 0.3 },
      { k: "max_fail_probability", v: 0.7 },
      { k: "require_turnstile", v: true, type: "bool" },
    ],
  },
  {
    title: "Provider scoring · weights ∑ = 1.00",
    prefix: "provider_scoring",
    items: [
      { k: "weights.cost", v: 0.3 },
      { k: "weights.success", v: 0.25 },
      { k: "weights.latency", v: 0.2 },
      { k: "weights.load", v: 0.15 },
      { k: "weights.freshness", v: 0.1 },
      { k: "metric_window_seconds", v: 300 },
      { k: "fallback_top_k", v: 3 },
      { k: "max_retries_per_job", v: 3 },
    ],
  },
  {
    title: "Circuit breaker",
    prefix: "circuit_breaker",
    items: [
      { k: "failure_threshold", v: 5 },
      { k: "initial_cooldown_seconds", v: 30 },
      { k: "max_cooldown_seconds", v: 600 },
      { k: "half_open_probe_concurrency", v: 1 },
    ],
  },
  {
    title: "Provider filter & retention",
    prefix: "—",
    items: [
      { k: "provider_filter.balance_min_threshold", v: 0.5, suffix: "CNY" },
      { k: "retention.job_retention_days", v: 30 },
      { k: "thumbnail.max_long_edge", v: 720, suffix: "px" },
      { k: "thumbnail.quality", v: 78 },
    ],
  },
];

export default function ConfigTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      {/* Emergency switches — top bill */}
      <div
        style={{
          border: "2px solid var(--ink)",
          background: "#fffdf7",
          boxShadow: "5px 5px 0 var(--ink)",
        }}
      >
        <div
          style={{
            padding: "12px 18px",
            background: "var(--ink)",
            color: "var(--banana)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <Icon name="bolt" size={14} />
          <span
            className="mono caps"
            style={{ fontSize: 12, letterSpacing: "0.18em", fontWeight: 700 }}
          >
            EMERGENCY SWITCHES
          </span>
          <span
            style={{
              marginLeft: "auto",
              fontSize: 11,
              color: "var(--paper)",
              opacity: 0.7,
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
            }}
          >
            two-step confirm · admins always bypass
          </span>
        </div>
        <div
          style={{
            padding: 18,
            display: "grid",
            gridTemplateColumns: "repeat(2, 1fr)",
            gap: 14,
          }}
        >
          {SWITCHES.map((s) => (
            <div
              key={s.k}
              style={{
                padding: "12px 14px",
                border: "1px solid var(--ink)",
                background: s.on ? "#f3d6d0" : "var(--paper-2)",
                display: "flex",
                alignItems: "center",
                gap: 14,
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>{s.label}</div>
                <div
                  className="mono"
                  style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 2 }}
                >
                  {s.k}
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--ink-2)",
                    marginTop: 4,
                    fontFamily: "var(--font-display)",
                    fontStyle: "italic",
                  }}
                >
                  {s.desc}
                </div>
              </div>
              <div
                style={{
                  width: 56,
                  height: 28,
                  border: "1px solid var(--ink)",
                  background: s.on ? "var(--bad)" : "var(--paper-3)",
                  position: "relative",
                  cursor: "pointer",
                  flexShrink: 0,
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    top: 1,
                    left: s.on ? 30 : 1,
                    width: 24,
                    height: 24,
                    background: s.on ? "var(--paper)" : "var(--ink)",
                    transition: "left 120ms",
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {SECTIONS.map((sec) => (
        <div key={sec.title}>
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
              style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.02em" }}
            >
              {sec.title}
            </div>
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              prefix · {sec.prefix}
            </span>
          </div>
          <Hair thick />
          <div
            style={{
              marginTop: 12,
              border: "1px solid var(--ink)",
              background: "#fffdf7",
            }}
          >
            {sec.items.map((item, i) => (
              <div
                key={item.k}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1.5fr 200px 1fr",
                  gap: 16,
                  alignItems: "center",
                  padding: "12px 16px",
                  borderTop: i ? "1px solid var(--rule)" : "none",
                }}
              >
                <div className="mono" style={{ fontSize: 12 }}>
                  <span style={{ color: "var(--ink-3)" }}>
                    {sec.prefix === "—" ? "" : `${sec.prefix}.`}
                  </span>
                  <span style={{ fontWeight: 700 }}>{item.k}</span>
                </div>
                <div>
                  {item.type === "bool" ? (
                    <div style={{ display: "flex", gap: 4 }}>
                      <button
                        className={`chip ${item.v ? "solid" : ""}`}
                        style={{ cursor: "pointer" }}
                      >
                        true
                      </button>
                      <button
                        className={`chip ${!item.v ? "solid" : ""}`}
                        style={{ cursor: "pointer" }}
                      >
                        false
                      </button>
                    </div>
                  ) : (
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <input
                        defaultValue={item.v}
                        className="inp"
                        style={{
                          height: 30,
                          fontFamily: "var(--font-mono)",
                          fontSize: 12,
                          fontWeight: 700,
                          padding: "0 10px",
                        }}
                      />
                      {item.suffix && (
                        <span
                          className="mono"
                          style={{ fontSize: 11, color: "var(--ink-3)" }}
                        >
                          {item.suffix}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                {item.hint && (
                  <div
                    className="mono"
                    style={{ fontSize: 11, color: "var(--ink-3)" }}
                  >
                    {item.hint}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Save bar */}
      <div
        style={{
          position: "sticky",
          bottom: -28,
          marginTop: 10,
          padding: "14px 18px",
          background: "var(--ink)",
          color: "var(--paper)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div className="mono" style={{ fontSize: 11 }}>
          <span style={{ color: "var(--banana)" }}>3 changes</span> · weights sum 1.00 ✓ ·
          will reload @ConfigCenter live
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn ghost"
            style={{ color: "var(--paper)", borderColor: "var(--paper-2)" }}
          >
            Discard
          </button>
          <button className="btn primary shadowed">Save & reload</button>
        </div>
      </div>
    </div>
  );
}
