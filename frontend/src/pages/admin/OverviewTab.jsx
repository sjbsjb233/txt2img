// Admin overview tab — bound to /api/admin/metrics/overview and the
// /api/admin/metrics/timeseries endpoint for the bottom chart.
//
// The polling cadence is intentionally generous (10 s) so an admin
// staring at the page sees fresh numbers without burning DB reads.
// A separate refresh button is offered for impatient operators.

import { useEffect, useMemo, useState } from "react";
import { Hair, StatusDot, TierPill } from "./atoms.jsx";
import * as metricsApi from "../../api/admin/metrics.js";

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function formatBytes(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(n);
  for (const u of units) {
    if (value < 1024 || u === units.at(-1)) {
      return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${u}`;
    }
    value /= 1024;
  }
  return `${value.toFixed(1)} TB`;
}

function formatPercent(value) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(Number(value) * 100).toFixed(1)}`;
}

function pillToneForCircuit(state) {
  if (state === "healthy") return "ok";
  if (state === "half_open") return "warn";
  if (state === "open" || state === "drained" || state === "disabled") return "muted";
  return "muted";
}

const TIER_ORDER = ["vip", "premium", "standard", "free"];
const TIER_WEIGHTS = { vip: 8, premium: 4, standard: 2, free: 1 };

const RANGE_LABELS = [
  { id: "24h", label: "24H" },
  { id: "7d", label: "7D" },
  { id: "30d", label: "30D" },
];

// ---------------------------------------------------------------------
// Tiny inline bar chart (no external deps; mirrors mock styling)
// ---------------------------------------------------------------------

function MiniBarChart({ points }) {
  const max = Math.max(1, ...points.map((p) => p.value || 0));
  const len = points.length;
  return (
    <div
      style={{
        height: 160,
        border: "1px solid var(--ink)",
        background: "#fffdf7",
        padding: "16px 20px",
        display: "flex",
        alignItems: "flex-end",
        gap: 2,
      }}
    >
      {points.map((p, i) => {
        const v = p.value || 0;
        const h = max > 0 ? Math.max(2, Math.round((v / max) * 100)) : 2;
        // Highlight the last 1/6 of the chart with banana so the user
        // can pick the recent end at a glance.
        const recent = i >= Math.floor(len * (5 / 6));
        return (
          <div
            key={p.ts}
            title={`${p.ts}\n${v.toFixed(2)}`}
            style={{
              flex: 1,
              height: `${h}%`,
              background: recent ? "var(--banana)" : "var(--ink)",
            }}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------
// Tab
// ---------------------------------------------------------------------

export default function OverviewTab() {
  const [overview, setOverview] = useState(null);
  const [overviewError, setOverviewError] = useState(null);
  const [overviewLoading, setOverviewLoading] = useState(true);

  const [timeseries, setTimeseries] = useState(null);
  const [tsError, setTsError] = useState(null);
  const [range, setRange] = useState("24h");

  // ---- fetchers ------------------------------------------------------

  async function fetchOverview() {
    setOverviewLoading(true);
    try {
      const data = await metricsApi.getOverview();
      setOverview(data);
      setOverviewError(null);
    } catch (err) {
      setOverviewError(err?.message || "Failed to load overview");
    } finally {
      setOverviewLoading(false);
    }
  }

  async function fetchTimeseries(r) {
    try {
      const data = await metricsApi.getTimeseries({
        metric: "jobs_count",
        range: r,
      });
      setTimeseries(data);
      setTsError(null);
    } catch (err) {
      setTsError(err?.message || "Failed to load timeseries");
    }
  }

  useEffect(() => {
    fetchOverview();
    const t = setInterval(fetchOverview, 10_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    fetchTimeseries(range);
    const t = setInterval(() => fetchTimeseries(range), 60_000);
    return () => clearInterval(t);
  }, [range]);

  // ---- derived -------------------------------------------------------

  const stats = useMemo(() => {
    if (!overview) return [];
    return [
      {
        label: "ACTIVE USERS · TODAY",
        value: String(overview.active_users_today),
      },
      { label: "JOBS · TODAY", value: String(overview.jobs_today) },
      { label: "IMAGES · TODAY", value: String(overview.images_today) },
      {
        label: "DISK · /APP/DATA",
        value: formatBytes(overview.disk_usage?.data_jobs_bytes ?? 0).split(" ")[0],
        unit: formatBytes(overview.disk_usage?.data_jobs_bytes ?? 0).split(" ")[1] || "GB",
        sub:
          overview.disk_usage?.free_bytes != null
            ? `${formatBytes(overview.disk_usage.free_bytes)} free`
            : null,
      },
      {
        label: "SUCCESS RATE · 24H",
        value: formatPercent(overview.success_rate_24h),
        unit: "%",
      },
    ];
  }, [overview]);

  const lanes = useMemo(() => {
    if (!overview?.queue_state) return [];
    return TIER_ORDER.map((tier) => ({
      tier,
      queued: overview.queue_state[tier]?.queued ?? 0,
      running: overview.queue_state[tier]?.running ?? 0,
      w: TIER_WEIGHTS[tier],
    }));
  }, [overview]);

  const providers = overview?.providers_summary || [];
  const healthyCount = providers.filter((p) => p.circuit_state === "healthy").length;
  const openCount = providers.filter((p) => p.circuit_state === "open").length;
  const drainedCount = providers.filter((p) => p.circuit_state === "drained").length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 36 }}>
      {/* ---------- top stats ---------- */}
      <section style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
        {overviewLoading && stats.length === 0
          ? Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                style={{
                  padding: 18,
                  background: "#fffdf7",
                  border: "1px solid var(--ink)",
                  height: 96,
                }}
              />
            ))
          : stats.map((s) => (
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
                {s.sub && (
                  <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 6 }}>
                    {s.sub}
                  </div>
                )}
              </div>
            ))}
      </section>

      {overviewError && (
        <div
          className="mono"
          style={{
            fontSize: 12,
            color: "var(--bad)",
            border: "1px solid var(--bad)",
            padding: 12,
          }}
        >
          {overviewError}
        </div>
      )}

      {/* ---------- lanes + providers ---------- */}
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
            {lanes.map((l, i) => {
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
                    <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
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
              {healthyCount} healthy · {openCount} open · {drainedCount} drained
            </div>
          </div>
          <Hair thick />
          <div
            style={{ marginTop: 10, border: "1px solid var(--ink)", background: "#fffdf7" }}
          >
            {providers.length === 0 ? (
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  color: "var(--ink-3)",
                  padding: 18,
                  textAlign: "center",
                }}
              >
                no providers configured
              </div>
            ) : (
              providers.map((p, i) => (
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
                    tone={pillToneForCircuit(p.circuit_state)}
                    label={p.circuit_state.toUpperCase()}
                  />
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                    {p.calls_5min} calls
                  </span>
                  <span
                    className="mono"
                    style={{
                      fontSize: 11,
                      color: "var(--ink)",
                      fontWeight: 700,
                    }}
                  >
                    {p.success_rate_5min != null
                      ? `${(p.success_rate_5min * 100).toFixed(1)}%`
                      : "—"}
                  </span>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                    {p.p50_ms_5min != null ? `${(p.p50_ms_5min / 1000).toFixed(1)}s` : "—"}
                  </span>
                  <span
                    className="mono"
                    style={{
                      fontSize: 11,
                      color: p.balance_cny < 0.5 ? "var(--bad)" : "var(--ink-3)",
                      textAlign: "right",
                    }}
                  >
                    ¥{Number(p.balance_cny || 0).toFixed(2)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      {/* ---------- timeseries chart ---------- */}
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
            JOBS · {range.toUpperCase()}
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {RANGE_LABELS.map((r) => (
              <button
                key={r.id}
                className={`chip ${range === r.id ? "solid" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => setRange(r.id)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        <Hair thick />
        {tsError && (
          <div
            className="mono"
            style={{
              marginTop: 12,
              fontSize: 12,
              color: "var(--bad)",
              border: "1px solid var(--bad)",
              padding: 8,
            }}
          >
            {tsError}
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          {timeseries?.points?.length ? (
            <MiniBarChart points={timeseries.points} />
          ) : (
            <div
              style={{
                height: 160,
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--ink-3)",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
              }}
            >
              no data
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
