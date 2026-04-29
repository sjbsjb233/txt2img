import Icon from "../../components/Icon.jsx";

const ROWS = [
  { ts: "2026-04-28 14:22:18", actor: "liuxi", action: "user.disable", target: "u_LzXcVbN1mPoI · @frank", ip: "10.0.0.4" },
  { ts: "2026-04-28 14:18:02", actor: "liuxi", action: "config.patch", target: "emergency.force_captcha_global → true", ip: "10.0.0.4" },
  { ts: "2026-04-28 13:51:40", actor: "liuxi", action: "provider.topup", target: "bltcy · ¥10.00", ip: "10.0.0.4" },
  { ts: "2026-04-28 13:48:12", actor: "liuxi", action: "provider.create", target: "azure_eu", ip: "10.0.0.4" },
  { ts: "2026-04-28 12:01:00", actor: "liuxi", action: "tier.patch", target: "premium.hard_quota 80→100", ip: "10.0.0.4" },
  { ts: "2026-04-28 11:42:30", actor: "liuxi (impersonating @claire_v)", action: "job.cancel", target: "j_a1b2c3d4e5f6", ip: "10.0.0.4", warn: true },
  { ts: "2026-04-28 11:30:14", actor: "liuxi", action: "user.impersonate", target: "u_8Hb1xQ4vRnZk · @claire_v · ttl 30m", ip: "10.0.0.4" },
  { ts: "2026-04-28 09:04:02", actor: "liuxi", action: "cleanup.execute", target: "older_than_days 30 · 1820 jobs · 8.8 GB", ip: "10.0.0.4" },
  { ts: "2026-04-28 08:55:18", actor: "liuxi", action: "announcement.publish", target: "ann_aBcD1234ef · Scheduled maintenance", ip: "10.0.0.4" },
];

const COL_TEMPLATE = "180px 200px 200px 1fr 100px";

export default function AuditTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: 1, minWidth: 240, maxWidth: 320 }}>
          <input
            className="inp"
            placeholder="search action / target"
            style={{ paddingLeft: 32, fontFamily: "var(--font-mono)" }}
          />
          <div style={{ position: "absolute", top: 11, left: 10 }}>
            <Icon name="search" size={14} stroke="var(--ink-3)" />
          </div>
        </div>
        <select
          className="inp"
          defaultValue="all"
          style={{ width: 160, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="all">actor · all</option>
        </select>
        <select
          className="inp"
          defaultValue="all"
          style={{ width: 200, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="all">action · all</option>
          <option>user.*</option>
          <option>provider.*</option>
          <option>config.patch</option>
          <option>tier.patch</option>
          <option>cleanup.execute</option>
        </select>
        <select
          className="inp"
          defaultValue="24h"
          style={{ width: 130, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="24h">since · 24h</option>
          <option>since · 7d</option>
          <option>since · 30d</option>
        </select>
        <div style={{ flex: 1 }} />
        <button className="btn">
          <Icon name="download" size={13} />
          Export
        </button>
      </div>

      <div style={{ border: "1px solid var(--ink)", background: "#fffdf7" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: COL_TEMPLATE,
            gap: 12,
            padding: "10px 16px",
            background: "var(--ink)",
            color: "var(--paper)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            fontWeight: 700,
          }}
        >
          <div>TS</div>
          <div>ACTOR</div>
          <div>ACTION</div>
          <div>TARGET</div>
          <div>IP</div>
        </div>
        {ROWS.map((r, i) => (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: COL_TEMPLATE,
              gap: 12,
              padding: "10px 16px",
              alignItems: "center",
              borderTop: i ? "1px solid var(--rule)" : "none",
              background: r.warn ? "#fbe9a155" : "transparent",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
            }}
          >
            <div style={{ color: "var(--ink-3)" }}>{r.ts}</div>
            <div
              style={{
                fontWeight: 700,
                color: r.warn ? "var(--warn)" : "var(--ink)",
              }}
            >
              {r.actor}
            </div>
            <div>
              <span
                style={{
                  padding: "2px 6px",
                  border: "1px solid var(--ink-4)",
                  background: "var(--paper-2)",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                {r.action}
              </span>
            </div>
            <div style={{ color: "var(--ink-2)" }}>{r.target}</div>
            <div style={{ color: "var(--ink-3)" }}>{r.ip}</div>
          </div>
        ))}
      </div>

      <div
        className="mono"
        style={{
          fontSize: 11,
          color: "var(--ink-3)",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>showing 1–9 of 1,420</span>
        <span>page 1 / 158</span>
      </div>
    </div>
  );
}
