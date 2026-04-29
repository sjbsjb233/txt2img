import { Hair, StatusDot, TierPill } from "./atoms.jsx";

const LANES = [
  { tier: "vip", queued: 0, running: 1, w: 8 },
  { tier: "premium", queued: 2, running: 4, w: 4 },
  { tier: "standard", queued: 12, running: 8, w: 2 },
  { tier: "free", queued: 31, running: 4, w: 1 },
];

const STATS = [
  { label: "ACTIVE USERS · TODAY", value: "42", delta: "+6" },
  { label: "JOBS · TODAY", value: "1,240", delta: "+184" },
  { label: "IMAGES · TODAY", value: "3,812", delta: "+412" },
  { label: "DISK · /APP/DATA", value: "14.3", unit: "GB", sub: "of 180 GB" },
  { label: "SUCCESS RATE · 24H", value: "98.1", unit: "%" },
];

const PROVIDERS = [
  { id: "bltcy", state: "healthy", calls: 142, sr: 98.6, p50: 8.2, balance: 3.42 },
  { id: "openai_official", state: "healthy", calls: 56, sr: 100, p50: 5.1, balance: 24.18 },
  { id: "azure_eu", state: "half_open", calls: 0, sr: null, p50: null, balance: 12.0 },
  { id: "relay_jp", state: "drained", calls: 0, sr: null, p50: null, balance: 0.12 },
];

export default function OverviewTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 36 }}>
      <section style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
        {STATS.map((s) => (
          <div
            key={s.label}
            style={{ padding: 18, background: "#fffdf7", border: "1px solid var(--ink)" }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.14em" }}
            >
              {s.label}
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginTop: 8 }}>
              <span
                className="ticker"
                style={{
                  fontSize: 38,
                  fontWeight: 900,
                  letterSpacing: "-0.04em",
                  lineHeight: 1,
                }}
              >
                {s.value}
              </span>
              {s.unit && (
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  {s.unit}
                </span>
              )}
            </div>
            {s.delta && (
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--ok)", marginTop: 6 }}
              >
                ↗ {s.delta} vs yesterday
              </div>
            )}
            {s.sub && (
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 6 }}
              >
                {s.sub}
              </div>
            )}
          </div>
        ))}
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              marginBottom: 10,
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
            >
              WFQ LANES · LIVE
            </div>
            <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              weight ratio 8:4:2:1
            </div>
          </div>
          <Hair thick />
          <div
            style={{
              marginTop: 10,
              border: "1px solid var(--ink)",
              background: "#fffdf7",
            }}
          >
            {LANES.map((l, i) => {
              const queueWidth = Math.min(100, l.queued * 3);
              const runWidth = Math.min(100, l.running * 6);
              return (
                <div
                  key={l.tier}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "100px 1fr 100px",
                    gap: 16,
                    alignItems: "center",
                    padding: "14px 16px",
                    borderTop: i ? "1px solid var(--rule)" : "none",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <TierPill tier={l.tier} />
                    <span
                      className="mono"
                      style={{ fontSize: 10, color: "var(--ink-3)" }}
                    >
                      w={l.w}
                    </span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", height: 18 }}>
                    <div
                      style={{
                        flex: 1,
                        height: 16,
                        background: "var(--paper-3)",
                        position: "relative",
                      }}
                    >
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          width: `${queueWidth}%`,
                          background:
                            "repeating-linear-gradient(135deg, var(--ink-3), var(--ink-3) 4px, transparent 4px, transparent 8px)",
                        }}
                      />
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          width: `${runWidth}%`,
                          background: "var(--banana)",
                        }}
                      />
                    </div>
                  </div>
                  <div
                    className="mono"
                    style={{
                      fontSize: 11,
                      textAlign: "right",
                      color: "var(--ink-3)",
                    }}
                  >
                    <span style={{ color: "var(--ink)", fontWeight: 700 }}>
                      {l.running}
                    </span>{" "}
                    run · {l.queued} q
                  </div>
                </div>
              );
            })}
          </div>
          <div
            className="mono"
            style={{
              fontSize: 10,
              color: "var(--ink-3)",
              marginTop: 10,
              display: "flex",
              gap: 14,
            }}
          >
            <span>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  background: "var(--banana)",
                  marginRight: 5,
                  verticalAlign: "middle",
                }}
              />
              running
            </span>
            <span>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  background:
                    "repeating-linear-gradient(135deg, var(--ink-3), var(--ink-3) 3px, transparent 3px, transparent 6px)",
                  marginRight: 5,
                  verticalAlign: "middle",
                }}
              />
              queued
            </span>
          </div>
        </div>

        <div>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              marginBottom: 10,
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
            >
              PROVIDERS · 5MIN WINDOW
            </div>
            <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              4 healthy · 0 open · 0 drained
            </div>
          </div>
          <Hair thick />
          <div
            style={{ marginTop: 10, border: "1px solid var(--ink)", background: "#fffdf7" }}
          >
            {PROVIDERS.map((p, i) => (
              <div
                key={p.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "120px 90px 60px 50px 70px 70px",
                  gap: 10,
                  alignItems: "center",
                  padding: "12px 16px",
                  borderTop: i ? "1px solid var(--rule)" : "none",
                }}
              >
                <div className="mono" style={{ fontSize: 12, fontWeight: 700 }}>
                  {p.id}
                </div>
                <StatusDot
                  tone={
                    p.state === "healthy"
                      ? "ok"
                      : p.state === "half_open"
                        ? "warn"
                        : "muted"
                  }
                  label={p.state.toUpperCase()}
                />
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  {p.calls} calls
                </span>
                <span
                  className="mono"
                  style={{
                    fontSize: 11,
                    color: p.sr != null ? "var(--ink)" : "var(--ink-4)",
                    fontWeight: 700,
                  }}
                >
                  {p.sr != null ? `${p.sr}%` : "—"}
                </span>
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  {p.p50 != null ? `${p.p50}s` : "—"}
                </span>
                <span
                  className="mono"
                  style={{
                    fontSize: 11,
                    color: p.balance < 0.5 ? "var(--bad)" : "var(--ink-3)",
                    textAlign: "right",
                  }}
                >
                  ¥{p.balance.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            marginBottom: 10,
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            JOBS · 24H
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {["24H", "7D", "30D"].map((r, i) => (
              <button
                key={r}
                className={`chip ${i === 0 ? "solid" : ""}`}
                style={{ cursor: "pointer" }}
              >
                {r}
              </button>
            ))}
          </div>
        </div>
        <Hair thick />
        <div
          style={{
            marginTop: 12,
            height: 160,
            border: "1px solid var(--ink)",
            background: "#fffdf7",
            padding: "16px 20px",
            display: "flex",
            alignItems: "flex-end",
            gap: 4,
          }}
        >
          {Array.from({ length: 48 }, (_, i) => {
            const h = 20 + Math.sin(i * 0.4) * 20 + Math.cos(i * 0.7) * 18 + 50;
            return (
              <div
                key={i}
                style={{
                  flex: 1,
                  height: `${h}%`,
                  background: i > 40 ? "var(--banana)" : "var(--ink)",
                }}
              />
            );
          })}
        </div>
        <div
          className="mono"
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 10,
            color: "var(--ink-3)",
            marginTop: 6,
          }}
        >
          <span>00:00</span>
          <span>06:00</span>
          <span>12:00</span>
          <span>18:00</span>
          <span style={{ color: "var(--ink)" }}>now</span>
        </div>
      </section>
    </div>
  );
}
