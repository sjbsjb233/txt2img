import Icon from "../../components/Icon.jsx";
import { Hair, TierPill } from "./atoms.jsx";

const USERS = [
  { id: "u_K7n9ZxQ2vPmL", username: "alice", display: "Alice Chen", role: "user", tier: "vip", status: "active", today: 47, soft: 100, hard: 200, last: "12m ago", jobs30: 1240 },
  { id: "u_3rQ9ZxK2pNmW", username: "bob", display: "Bob — design", role: "user", tier: "premium", status: "active", today: 23, soft: 50, hard: 100, last: "2h ago", jobs30: 412 },
  { id: "u_8Hb1xQ4vRnZk", username: "claire_v", display: null, role: "user", tier: "premium", status: "active", today: 51, soft: 50, hard: 100, last: "3m ago", jobs30: 980, soft_breached: true },
  { id: "u_X2pNvK7tMqLs", username: "dee", display: "Dee", role: "user", tier: "standard", status: "active", today: 12, soft: 20, hard: 40, last: "1d ago", jobs30: 88 },
  { id: "u_Q9wErTyUiOpA", username: "eve_test", display: null, role: "user", tier: "free", status: "active", today: 10, soft: 8, hard: 10, last: "30m ago", jobs30: 142, soft_breached: true, hard_near: true },
  { id: "u_LzXcVbN1mPoI", username: "frank", display: "Frank R.", role: "user", tier: "free", status: "disabled", today: 0, soft: 8, hard: 10, last: "8d ago", jobs30: 4 },
  { id: "u_RtYuIoP2sFgH", username: "liuxi", display: "liuxi", role: "admin", tier: "vip", status: "active", today: 12, soft: 100, hard: 200, last: "now", jobs30: 320 },
];

const COL_TEMPLATE = "240px 110px 110px 1fr 130px 110px 90px";

export default function UsersTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Toolbar */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: 1, minWidth: 280, maxWidth: 360 }}>
          <input
            className="inp"
            placeholder="search username or display name"
            style={{ paddingLeft: 32, fontFamily: "var(--font-mono)" }}
          />
          <div style={{ position: "absolute", top: 11, left: 10 }}>
            <Icon name="search" size={14} stroke="var(--ink-3)" />
          </div>
        </div>
        <select
          className="inp"
          defaultValue="all"
          style={{ width: 140, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="all">tier · all</option>
          <option>vip</option>
          <option>premium</option>
          <option>standard</option>
          <option>free</option>
        </select>
        <select
          className="inp"
          defaultValue="all"
          style={{ width: 140, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="all">status · all</option>
          <option>active</option>
          <option>disabled</option>
          <option>deleted</option>
        </select>
        <select
          className="inp"
          defaultValue="last"
          style={{ width: 200, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="last">sort · last_login_at desc</option>
          <option>created_at desc</option>
          <option>today_count desc</option>
        </select>
        <div style={{ flex: 1 }} />
        <button className="btn">
          <Icon name="upload" size={13} /> Bulk
        </button>
        <button className="btn primary shadowed">
          <Icon name="plus" size={13} /> New user
        </button>
      </div>

      {/* Table */}
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
          <div>USER</div>
          <div>ROLE</div>
          <div>TIER</div>
          <div>TODAY USAGE (soft / hard)</div>
          <div>LAST LOGIN</div>
          <div>30D JOBS</div>
          <div style={{ textAlign: "right" }}>—</div>
        </div>
        {USERS.map((u, i) => {
          const pct = (u.today / u.hard) * 100;
          const softPct = (u.soft / u.hard) * 100;
          return (
            <div
              key={u.id}
              style={{
                display: "grid",
                gridTemplateColumns: COL_TEMPLATE,
                gap: 12,
                padding: "12px 16px",
                alignItems: "center",
                borderTop: i ? "1px solid var(--rule)" : "none",
                background: u.status === "disabled" ? "var(--paper-2)" : "#fffdf7",
                opacity: u.status === "disabled" ? 0.6 : 1,
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div
                    style={{
                      width: 26,
                      height: 26,
                      background: u.role === "admin" ? "var(--ink)" : "var(--paper-3)",
                      color: u.role === "admin" ? "var(--banana)" : "var(--ink)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      fontWeight: 700,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      border: "1px solid var(--ink)",
                      flexShrink: 0,
                    }}
                  >
                    {u.username.slice(0, 2).toUpperCase()}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.2 }}>
                      {u.display || u.username}
                    </div>
                    <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                      @{u.username}
                    </div>
                  </div>
                </div>
              </div>
              <div>
                {u.role === "admin" ? (
                  <span className="chip solid" style={{ fontSize: 9 }}>
                    ADMIN
                  </span>
                ) : (
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                    user
                  </span>
                )}
              </div>
              <div>
                <TierPill tier={u.tier} />
              </div>
              <div>
                <div
                  style={{
                    height: 6,
                    background: "var(--paper-3)",
                    position: "relative",
                    border: "1px solid var(--ink)",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: `${Math.min(100, pct)}%`,
                      background: u.hard_near
                        ? "var(--bad)"
                        : u.soft_breached
                          ? "var(--warn)"
                          : "var(--ink)",
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      left: `${softPct}%`,
                      top: -2,
                      bottom: -2,
                      width: 1,
                      background: "var(--ink)",
                    }}
                  />
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 4 }}
                >
                  <span
                    style={{
                      color: u.hard_near
                        ? "var(--bad)"
                        : u.soft_breached
                          ? "var(--warn)"
                          : "var(--ink)",
                      fontWeight: 700,
                    }}
                  >
                    {u.today}
                  </span>{" "}
                  / soft {u.soft} / hard {u.hard}
                  {u.soft_breached && (
                    <span style={{ marginLeft: 6, color: "var(--warn)" }}>
                      · soft breach
                    </span>
                  )}
                </div>
              </div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                {u.last}
              </div>
              <div className="mono" style={{ fontSize: 12, fontWeight: 700 }}>
                {u.jobs30.toLocaleString()}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 4 }}>
                <button className="btn sm ghost" title="Impersonate">
                  <Icon name="eye" size={12} />
                </button>
                <button className="btn sm ghost" title="More">
                  <Icon name="more" size={12} />
                </button>
              </div>
            </div>
          );
        })}
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
        <span>showing 1–7 of 142</span>
        <span>page 1 / 21</span>
      </div>

      {/* Focused user drawer */}
      <div style={{ marginTop: 16 }}>
        <div
          className="mono caps"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            letterSpacing: "0.16em",
            marginBottom: 10,
          }}
        >
          FOCUSED · @claire_v
        </div>
        <Hair thick />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.4fr 1fr",
            gap: 16,
            marginTop: 12,
          }}
        >
          <div
            style={{ border: "1px solid var(--ink)", background: "#fffdf7", padding: 18 }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 14,
                justifyContent: "space-between",
              }}
            >
              <div>
                <div
                  className="display"
                  style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.02em" }}
                >
                  claire_v
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}
                >
                  u_8Hb1xQ4vRnZk · created 2026-02-14
                </div>
                <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
                  <TierPill tier="premium" />
                  <span className="chip ok">ACTIVE</span>
                  <span className="chip warn">SOFT BREACH</span>
                </div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn sm">Reset password</button>
                <button className="btn sm">Disable</button>
                <button className="btn sm ink">Impersonate · 30m</button>
              </div>
            </div>
            <div
              style={{
                marginTop: 18,
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
              }}
            >
              {[
                { l: "tier", v: "premium" },
                { l: "override soft_quota", v: "— (use tier default 50)" },
                { l: "override hard_quota", v: "— (use tier default 100)" },
                { l: "display name", v: "—" },
              ].map((f) => (
                <div key={f.l}>
                  <div
                    className="mono caps"
                    style={{ fontSize: 9, color: "var(--ink-3)" }}
                  >
                    {f.l}
                  </div>
                  <input
                    className="inp"
                    defaultValue={f.v}
                    style={{
                      marginTop: 4,
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                    }}
                  />
                </div>
              ))}
            </div>
          </div>

          <div
            style={{ border: "1px solid var(--ink)", background: "#fffdf7", padding: 18 }}
          >
            <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              30 DAYS · JOBS PER DAY
            </div>
            <div
              style={{
                marginTop: 10,
                height: 80,
                display: "flex",
                alignItems: "flex-end",
                gap: 2,
              }}
            >
              {Array.from({ length: 30 }, (_, i) => {
                const h = 20 + Math.sin(i * 0.5) * 25 + Math.cos(i * 0.3) * 20 + 30;
                return (
                  <div
                    key={i}
                    style={{
                      flex: 1,
                      height: `${h}%`,
                      background: i > 26 ? "var(--banana)" : "var(--ink-2)",
                    }}
                  />
                );
              })}
            </div>
            <div
              className="mono"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                marginTop: 6,
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <span>30d ago</span>
              <span>980 jobs · 96.3% ok · avg 11.2s</span>
              <span>today</span>
            </div>
            <Hair />
            <div
              style={{
                marginTop: 12,
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
              }}
            >
              <div>
                <div
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  TOP MODELS
                </div>
                <div
                  style={{
                    marginTop: 6,
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                  }}
                >
                  <div className="mono" style={{ fontSize: 11 }}>1 · gemini-3-pro-image · 612</div>
                  <div className="mono" style={{ fontSize: 11 }}>2 · gpt-image-2 · 248</div>
                  <div className="mono" style={{ fontSize: 11 }}>3 · gemini-3.1-flash · 120</div>
                </div>
              </div>
              <div>
                <div
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  TOP PROVIDERS
                </div>
                <div
                  style={{
                    marginTop: 6,
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                  }}
                >
                  <div className="mono" style={{ fontSize: 11 }}>1 · bltcy · 712</div>
                  <div className="mono" style={{ fontSize: 11 }}>2 · openai_official · 198</div>
                  <div className="mono" style={{ fontSize: 11 }}>3 · azure_eu · 70</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
