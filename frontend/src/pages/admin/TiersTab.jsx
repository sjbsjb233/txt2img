import { useCallback, useEffect, useMemo, useState } from "react";
import * as adminTiers from "../../api/admin/tiers.js";
import { TierPill } from "./atoms.jsx";

// Editable fields exposed via PATCH /api/admin/tiers/<tier>. ``slo_p95_ms``
// is nullable (Free tier sends null) — every other field requires an integer.
const FIELDS = [
  { k: "weight", help: "WFQ抽 lane 相对频率", nullable: false },
  { k: "max_concurrency", help: "用户同时跑的 Job 数", nullable: false },
  { k: "max_queue", help: "用户在 lane 里排队的 Job 数", nullable: false },
  { k: "soft_quota", help: "每日软限 · 触发恶心模式", nullable: false },
  { k: "hard_quota", help: "每日硬限 · 直接拒绝", nullable: false },
  { k: "slo_p95_ms", help: "P95 延迟告警阈值 · 留空表示无 SLO", nullable: true },
];

const TIER_ORDER = ["vip", "premium", "standard", "free"];

function toEditable(tier) {
  return {
    tier: tier.tier,
    weight: String(tier.weight ?? ""),
    max_concurrency: String(tier.max_concurrency ?? ""),
    max_queue: String(tier.max_queue ?? ""),
    soft_quota: String(tier.soft_quota ?? ""),
    hard_quota: String(tier.hard_quota ?? ""),
    slo_p95_ms: tier.slo_p95_ms == null ? "" : String(tier.slo_p95_ms),
  };
}

function diffOne(server, edited) {
  const out = {};
  for (const f of FIELDS) {
    const src = server[f.k];
    const cur = edited[f.k];
    let parsed;
    if (cur === "") {
      if (!f.nullable) continue;
      parsed = null;
    } else {
      const n = Number(cur);
      if (!Number.isFinite(n) || !Number.isInteger(n)) continue;
      parsed = n;
    }
    const same = src == null && parsed === null;
    if (!same && src !== parsed) {
      out[f.k] = parsed;
    }
  }
  return out;
}

function isValidValue(v, field) {
  if (v === "") return field.nullable;
  const n = Number(v);
  return Number.isFinite(n) && Number.isInteger(n) && n >= 0;
}

export default function TiersTab() {
  const [server, setServer] = useState([]);
  const [edited, setEdited] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [savedAt, setSavedAt] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminTiers.listTiers();
      const tiers = res.tiers || [];
      // Order vip → premium → standard → free for stable display.
      const ordered = TIER_ORDER.map(
        (t) => tiers.find((row) => row.tier === t) || null,
      ).filter(Boolean);
      setServer(ordered);
      const next = {};
      for (const t of ordered) next[t.tier] = toEditable(t);
      setEdited(next);
    } catch (err) {
      setError(err.message || "Failed to load tiers.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const dirtyTiers = useMemo(() => {
    const dirty = {};
    for (const row of server) {
      const patch = diffOne(row, edited[row.tier] || {});
      if (Object.keys(patch).length > 0) dirty[row.tier] = patch;
    }
    return dirty;
  }, [server, edited]);

  const allValid = useMemo(() => {
    for (const row of server) {
      const cur = edited[row.tier] || {};
      for (const f of FIELDS) {
        if (!isValidValue(cur[f.k] ?? "", f)) return false;
      }
    }
    return true;
  }, [server, edited]);

  const totalDirty = Object.keys(dirtyTiers).length;

  const onChange = (tier, key, value) => {
    setEdited((prev) => ({
      ...prev,
      [tier]: { ...prev[tier], [key]: value },
    }));
  };

  const onSave = async () => {
    if (!allValid || totalDirty === 0) return;
    setSaving(true);
    setError(null);
    try {
      // PATCH each dirty tier sequentially. Backend hot-reloads
      // TierConfig on each call so the order doesn't matter.
      for (const [tier, patch] of Object.entries(dirtyTiers)) {
        await adminTiers.patchTier(tier, patch);
      }
      setSavedAt(new Date());
      await refresh();
    } catch (err) {
      setError(err.message || "Failed to save tiers.");
    } finally {
      setSaving(false);
    }
  };

  const onDiscard = () => {
    const next = {};
    for (const row of server) next[row.tier] = toEditable(row);
    setEdited(next);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            PATCH /api/admin/tiers/&lt;tier&gt;
          </div>
          <div
            className="display"
            style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.02em", marginTop: 6 }}
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

      {error && (
        <div
          style={{
            padding: 10,
            border: "1px solid var(--bad)",
            color: "var(--bad)",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      <div style={{ border: "1px solid var(--ink)", background: "#fffdf7", overflowX: "auto" }}>
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
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td
                  colSpan={FIELDS.length + 1}
                  style={{ padding: 18, color: "var(--ink-3)", fontStyle: "italic" }}
                >
                  loading tiers…
                </td>
              </tr>
            )}
            {!loading &&
              server.map((row, i) => {
                const cur = edited[row.tier] || {};
                const dirty = dirtyTiers[row.tier];
                return (
                  <tr
                    key={row.tier}
                    style={{ borderTop: i ? "1px solid var(--rule)" : "none" }}
                  >
                    <td style={{ padding: "12px 14px" }}>
                      <TierPill tier={row.tier} />
                      {dirty && (
                        <span
                          className="mono"
                          style={{
                            marginLeft: 8,
                            fontSize: 9,
                            color: "var(--banana-deep)",
                            fontWeight: 700,
                          }}
                        >
                          DIRTY
                        </span>
                      )}
                    </td>
                    {FIELDS.map((f) => {
                      const value = cur[f.k] ?? "";
                      const valid = isValidValue(value, f);
                      return (
                        <td
                          key={f.k}
                          style={{ padding: "8px 14px", textAlign: "right" }}
                        >
                          <input
                            value={value}
                            placeholder={f.nullable ? "null" : ""}
                            onChange={(e) => onChange(row.tier, f.k, e.target.value)}
                            style={{
                              width: 86,
                              padding: "4px 8px",
                              fontFamily: "var(--font-mono)",
                              fontSize: 12,
                              fontWeight: 700,
                              border: `1px solid ${valid ? "var(--ink-4)" : "var(--bad)"}`,
                              background: "transparent",
                              textAlign: "right",
                              color: valid ? "var(--ink)" : "var(--bad)",
                            }}
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          {totalDirty > 0
            ? `${totalDirty} tier(s) modified · changes apply immediately via @TierConfig.reload()`
            : savedAt
              ? `saved · @TierConfig.reload() at ${savedAt.toLocaleTimeString()}`
              : "changes apply immediately via @TierConfig.reload()"}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn"
            onClick={onDiscard}
            disabled={saving || totalDirty === 0}
          >
            Discard
          </button>
          <button
            type="button"
            className="btn primary shadowed"
            onClick={onSave}
            disabled={saving || !allValid || totalDirty === 0}
          >
            {saving ? "Saving…" : "Save tiers"}
          </button>
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
