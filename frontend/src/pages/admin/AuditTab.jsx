// Admin audit log feed bound to /api/admin/audit (FE-07c).
//
// Replaces the mock list with paginated, filterable real data. Filters
// match the backend's accepted params: actor (id or username), action
// (exact or wildcard), since (24h / 7d / 30d shortcuts), and pagination.

import { useEffect, useMemo, useState } from "react";
import Icon from "../../components/Icon.jsx";
import * as auditApi from "../../api/admin/audit.js";

const COL_TEMPLATE = "180px 200px 200px 1fr 100px";

const SINCE_PRESETS = [
  { id: "24h", label: "since · 24h", hours: 24 },
  { id: "7d", label: "since · 7d", hours: 24 * 7 },
  { id: "30d", label: "since · 30d", hours: 24 * 30 },
  { id: "all", label: "since · all", hours: null },
];

const ACTION_PRESETS = [
  { id: "all", label: "action · all", value: null },
  { id: "user", label: "user.*", value: "user.*" },
  { id: "provider", label: "provider.*", value: "provider.*" },
  { id: "config", label: "config.*", value: "config.*" },
  { id: "tier", label: "tier.*", value: "tier.*" },
  { id: "cleanup", label: "cleanup.*", value: "cleanup.*" },
  { id: "ann", label: "announcement.*", value: "announcement.*" },
];

function isoSinceHours(hours) {
  if (hours == null) return null;
  return new Date(Date.now() - hours * 3600 * 1000).toISOString();
}

function formatTs(value) {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toISOString().slice(0, 19).replace("T", " ");
  } catch {
    return String(value);
  }
}

function describeTarget(entry) {
  // Compose a human-readable target string from the audit row. The
  // payload column carries the rich detail; we surface a short version
  // that fits in one line.
  const parts = [];
  if (entry.target_id) parts.push(entry.target_id);
  if (entry.payload && typeof entry.payload === "object") {
    const p = entry.payload;
    // Common fields the backend writes for various actions.
    if (p.username) parts.push(`@${p.username}`);
    if (p.tier) parts.push(`tier=${p.tier}`);
    if (p.amount_cny != null) parts.push(`¥${p.amount_cny}`);
    if (p.title) parts.push(`"${p.title}"`);
    if (p.changes) {
      const keys = Object.keys(p.changes).slice(0, 3);
      if (keys.length) parts.push(keys.join(", "));
    }
    if (p.keys && Array.isArray(p.keys)) {
      parts.push(p.keys.slice(0, 3).join(", "));
    }
  }
  return parts.length ? parts.join(" · ") : "—";
}

const PAGE_SIZE = 50;

export default function AuditTab() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [actorQuery, setActorQuery] = useState("");
  const [actionPreset, setActionPreset] = useState("all");
  const [sincePreset, setSincePreset] = useState("24h");
  const [page, setPage] = useState(1);

  // Live "search" query input — debounced via a small setTimeout in the
  // effect below so each keystroke doesn't fire a fetch.
  const [searchInput, setSearchInput] = useState("");

  // Reset page when any filter changes so we don't end up on page 12
  // of a now-empty result set.
  useEffect(() => {
    setPage(1);
  }, [actorQuery, actionPreset, sincePreset]);

  useEffect(() => {
    const handle = setTimeout(() => setActorQuery(searchInput.trim()), 250);
    return () => clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const params = {
      page,
      pageSize: PAGE_SIZE,
    };
    const sincePresetObj = SINCE_PRESETS.find((s) => s.id === sincePreset);
    if (sincePresetObj?.hours != null) {
      params.since = isoSinceHours(sincePresetObj.hours);
    }
    const actionPresetObj = ACTION_PRESETS.find((a) => a.id === actionPreset);
    if (actionPresetObj?.value) {
      params.action = actionPresetObj.value;
    }
    if (actorQuery) {
      params.actor = actorQuery;
    }
    auditApi
      .listAudit(params)
      .then((data) => {
        if (cancelled) return;
        setItems(data?.items || []);
        setTotal(data?.total || 0);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.message || "Failed to load audit log");
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [actorQuery, actionPreset, sincePreset, page]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const showingFrom = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const showingTo = Math.min(total, page * PAGE_SIZE);

  const displayedRows = useMemo(
    () =>
      items.map((r) => ({
        ...r,
        actorLabel: r.actor_username
          ? `${r.actor_username}`
          : r.actor_user_id || "(unknown)",
        targetLabel: describeTarget(r),
        warn: !!r.payload?.impersonator || r.action === "user.impersonate",
      })),
    [items],
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: 1, minWidth: 240, maxWidth: 320 }}>
          <input
            className="inp"
            placeholder="search by actor (username or u_…)"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            style={{ paddingLeft: 32, fontFamily: "var(--font-mono)" }}
          />
          <div style={{ position: "absolute", top: 11, left: 10 }}>
            <Icon name="search" size={14} stroke="var(--ink-3)" />
          </div>
        </div>
        <select
          className="inp"
          value={actionPreset}
          onChange={(e) => setActionPreset(e.target.value)}
          style={{ width: 200, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          {ACTION_PRESETS.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </select>
        <select
          className="inp"
          value={sincePreset}
          onChange={(e) => setSincePreset(e.target.value)}
          style={{ width: 140, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          {SINCE_PRESETS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        <div style={{ flex: 1 }} />
        <button
          className="btn"
          onClick={() => setPage((n) => n)}
          title="Refresh"
          disabled={loading}
        >
          <Icon name="refresh" size={13} />
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div
          className="mono"
          style={{
            fontSize: 12,
            color: "var(--bad)",
            border: "1px solid var(--bad)",
            padding: 12,
          }}
        >
          {error}
        </div>
      )}

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
        {displayedRows.length === 0 && !loading && (
          <div
            className="mono"
            style={{
              padding: 24,
              textAlign: "center",
              fontSize: 12,
              color: "var(--ink-3)",
            }}
          >
            no audit entries match the current filters
          </div>
        )}
        {displayedRows.map((r) => (
          <div
            key={r.id}
            style={{
              display: "grid",
              gridTemplateColumns: COL_TEMPLATE,
              gap: 12,
              padding: "10px 16px",
              alignItems: "center",
              borderTop: "1px solid var(--rule)",
              background: r.warn ? "#fbe9a155" : "transparent",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
            }}
          >
            <div style={{ color: "var(--ink-3)" }}>{formatTs(r.ts)}</div>
            <div
              style={{
                fontWeight: 700,
                color: r.warn ? "var(--warn)" : "var(--ink)",
              }}
              title={r.actor_user_id}
            >
              {r.actorLabel}
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
            <div style={{ color: "var(--ink-2)" }}>{r.targetLabel}</div>
            <div style={{ color: "var(--ink-3)" }}>{r.ip || "—"}</div>
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
          alignItems: "center",
        }}
      >
        <span>
          showing {showingFrom}–{showingTo} of {total}
        </span>
        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button
            className="btn sm"
            onClick={() => setPage((n) => Math.max(1, n - 1))}
            disabled={page <= 1}
          >
            ‹ prev
          </button>
          <span>
            page {page} / {pageCount}
          </span>
          <button
            className="btn sm"
            onClick={() => setPage((n) => Math.min(pageCount, n + 1))}
            disabled={page >= pageCount}
          >
            next ›
          </button>
        </span>
      </div>
    </div>
  );
}
