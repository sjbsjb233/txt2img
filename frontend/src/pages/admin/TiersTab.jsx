import { TierPill } from "./atoms.jsx";

const TIERS = [
  { tier: "vip", w: 8, conc: 4, queue: 10, soft: 100, hard: 200, slo: 30000, users: 8 },
  { tier: "premium", w: 4, conc: 2, queue: 5, soft: 50, hard: 100, slo: 60000, users: 24 },
  { tier: "standard", w: 2, conc: 1, queue: 3, soft: 20, hard: 40, slo: 180000, users: 56 },
  { tier: "free", w: 1, conc: 1, queue: 3, soft: 8, hard: 10, slo: null, users: 54 },
];

const FIELDS = [
  { k: "weight", help: "WFQ抽 lane 相对频率" },
  { k: "max_concurrency", help: "用户同时跑的 Job 数" },
  { k: "max_queue", help: "用户在 lane 里排队的 Job 数" },
  { k: "soft_quota", help: "每日软限 · 触发恶心模式" },
  { k: "hard_quota", help: "每日硬限 · 直接拒绝" },
  { k: "slo_p95_ms", help: "P95 延迟告警阈值" },
];

export default function TiersTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            PATCH /api/admin/tiers/&lt;tier&gt;
          </div>
          <div
            className="display"
            style={{
              fontSize: 28,
              fontWeight: 800,
              letterSpacing: "-0.02em",
              marginTop: 6,
            }}
          >
            Edit four tiers. Hot-reload, no restart.
          </div>
          <div
            style={{
              fontSize: 13,
              color: "var(--ink-3)",
              marginTop: 6,
              fontStyle: "italic",
              fontFamily: "var(--font-display)",
            }}
          >
            Per-user overrides live on the user record (override_soft_quota /
            override_hard_quota), not here.
          </div>
        </div>
      </div>

      <div
        style={{ border: "1px solid var(--ink)", background: "#fffdf7", overflowX: "auto" }}
      >
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
          }}
        >
          <thead>
            <tr style={{ background: "var(--ink)", color: "var(--paper)" }}>
              <th
                style={{
                  textAlign: "left",
                  padding: "10px 14px",
                  fontSize: 10,
                  letterSpacing: "0.14em",
                  fontWeight: 700,
                }}
              >
                TIER
              </th>
              {FIELDS.map((f) => (
                <th
                  key={f.k}
                  style={{
                    textAlign: "right",
                    padding: "10px 14px",
                    fontSize: 10,
                    letterSpacing: "0.14em",
                    fontWeight: 700,
                  }}
                >
                  {f.k}
                </th>
              ))}
              <th
                style={{
                  textAlign: "right",
                  padding: "10px 14px",
                  fontSize: 10,
                  letterSpacing: "0.14em",
                  fontWeight: 700,
                }}
              >
                USERS
              </th>
            </tr>
          </thead>
          <tbody>
            {TIERS.map((t, i) => (
              <tr
                key={t.tier}
                style={{ borderTop: i ? "1px solid var(--rule)" : "none" }}
              >
                <td style={{ padding: "12px 14px" }}>
                  <TierPill tier={t.tier} />
                </td>
                {[t.w, t.conc, t.queue, t.soft, t.hard, t.slo].map((v, j) => (
                  <td key={j} style={{ padding: "8px 14px", textAlign: "right" }}>
                    <input
                      defaultValue={v ?? ""}
                      placeholder={v == null ? "null" : ""}
                      style={{
                        width: 86,
                        padding: "4px 8px",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                        fontWeight: 700,
                        border: "1px solid var(--ink-4)",
                        background: "transparent",
                        textAlign: "right",
                      }}
                    />
                  </td>
                ))}
                <td
                  style={{
                    padding: "12px 14px",
                    textAlign: "right",
                    color: "var(--ink-3)",
                  }}
                >
                  {t.users}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          changes apply immediately via @TierConfig.reload()
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn">Discard</button>
          <button className="btn primary shadowed">Save tiers</button>
        </div>
      </div>

      <div
        style={{
          marginTop: 8,
          padding: "14px 16px",
          background: "var(--paper-2)",
          border: "1px dashed var(--ink-4)",
        }}
      >
        <div
          className="mono caps"
          style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.14em" }}
        >
          FIELD CHEATSHEET
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, 1fr)",
            gap: 6,
            marginTop: 8,
          }}
        >
          {FIELDS.map((f) => (
            <div
              key={f.k}
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-2)" }}
            >
              <span style={{ color: "var(--ink)", fontWeight: 700 }}>{f.k}</span> · {f.help}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
