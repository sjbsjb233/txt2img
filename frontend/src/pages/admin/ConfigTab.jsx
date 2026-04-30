import { useCallback, useEffect, useMemo, useState } from "react";
import Icon from "../../components/Icon.jsx";
import * as adminConfig from "../../api/admin/config.js";
import { Hair } from "./atoms.jsx";

// Field metadata for each known config key. Mirrors the schema in
// app/domain/config_center.py — kept in sync by hand for now (a
// future PR could surface the validator metadata over the API).
const SECTIONS = [
  {
    title: "Scheduler",
    prefix: "scheduler",
    items: [
      { k: "global_max_workers", type: "int", hint: "全局并发硬上限" },
      { k: "min_share_per_lane", type: "float", hint: "每条 lane 至少 5% 吞吐" },
      { k: "deadline_promotion_seconds", type: "int", hint: "Free 等过即升一级 lane" },
    ],
  },
  {
    title: "Soft penalty",
    prefix: "soft_penalty",
    items: [
      { k: "base_delay_seconds", type: "float" },
      { k: "max_delay_seconds", type: "float" },
      { k: "base_fail_probability", type: "float" },
      { k: "max_fail_probability", type: "float" },
      { k: "require_turnstile", type: "bool" },
    ],
  },
  {
    title: "Provider scoring · weights ∑ = 1.00",
    prefix: "provider_scoring",
    items: [
      { k: "weights.cost", type: "float", weight: true },
      { k: "weights.success", type: "float", weight: true },
      { k: "weights.latency", type: "float", weight: true },
      { k: "weights.load", type: "float", weight: true },
      { k: "weights.freshness", type: "float", weight: true },
      { k: "metric_window_seconds", type: "int" },
      { k: "fallback_top_k", type: "int" },
      { k: "max_retries_per_job", type: "int" },
    ],
  },
  {
    title: "Circuit breaker",
    prefix: "circuit_breaker",
    items: [
      { k: "failure_threshold", type: "int" },
      { k: "initial_cooldown_seconds", type: "int" },
      { k: "max_cooldown_seconds", type: "int" },
      { k: "half_open_probe_concurrency", type: "int" },
    ],
  },
  {
    title: "Provider filter & retention",
    prefix: "—",
    items: [
      {
        k: "provider_filter.balance_min_threshold",
        type: "float",
        suffix: "CNY",
      },
      { k: "retention.job_retention_days", type: "int" },
      { k: "thumbnail.max_long_edge", type: "int", suffix: "px" },
      { k: "thumbnail.quality", type: "int" },
    ],
  },
];

const EMERGENCY_SWITCHES = [
  {
    k: "emergency.pause_generation",
    label: "Pause generation",
    desc: "POST /api/jobs → 403. Running jobs untouched.",
  },
  {
    k: "emergency.pause_image_access",
    label: "Pause image access",
    desc: "/thumb /original → 403 for non-admin.",
  },
  {
    k: "emergency.block_new_member_login",
    label: "Block non-admin login",
    desc: "Only admins can sign in.",
  },
  {
    k: "emergency.force_captcha_global",
    label: "Force global captcha",
    desc: "precheck always require_captcha.",
  },
];

const WEIGHT_KEYS = [
  "provider_scoring.weights.cost",
  "provider_scoring.weights.success",
  "provider_scoring.weights.latency",
  "provider_scoring.weights.load",
  "provider_scoring.weights.freshness",
];

function fullKey(prefix, k) {
  return prefix === "—" ? k : `${prefix}.${k}`;
}

function parseValue(raw, type) {
  if (type === "bool") return Boolean(raw);
  if (type === "int") {
    const n = Number(raw);
    if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
    return n;
  }
  if (type === "float") {
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    return n;
  }
  return raw;
}

function valuesEqual(a, b) {
  // strict equality is fine for our scalar config values
  return a === b;
}

export default function ConfigTab() {
  const [server, setServer] = useState({});
  const [edited, setEdited] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [savedAt, setSavedAt] = useState(null);
  const [confirm, setConfirm] = useState(null); // { key, label, nextValue }

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminConfig.getConfig();
      setServer(data || {});
      setEdited({});
    } catch (err) {
      setError(err.message || "Failed to load config.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const dirtyKeys = useMemo(
    () =>
      Object.entries(edited).filter(
        ([k, v]) => v !== undefined && !valuesEqual(server[k], v),
      ),
    [server, edited],
  );

  // Validate weight invariants client-side so admins see the issue
  // before they hit Save (the backend re-checks).
  const weightSum = useMemo(() => {
    let sum = 0;
    for (const k of WEIGHT_KEYS) {
      const v = edited[k] !== undefined ? edited[k] : server[k];
      if (typeof v === "number" && Number.isFinite(v)) sum += v;
    }
    return sum;
  }, [server, edited]);
  const weightsOk = Math.abs(weightSum - 1) < 1e-6;

  const setEditedKey = (key, value) => {
    setEdited((prev) => {
      const next = { ...prev };
      if (valuesEqual(server[key], value)) {
        delete next[key];
      } else {
        next[key] = value;
      }
      return next;
    });
  };

  const onSave = async () => {
    if (!weightsOk) {
      setError("provider_scoring.weights.* must sum to 1.00 before saving.");
      return;
    }
    if (dirtyKeys.length === 0) return;
    setSaving(true);
    setError(null);
    try {
      const payload = Object.fromEntries(dirtyKeys);
      await adminConfig.patchConfig(payload);
      setSavedAt(new Date());
      await refresh();
    } catch (err) {
      setError(err.message || "Failed to save config.");
    } finally {
      setSaving(false);
    }
  };

  const onDiscard = () => setEdited({});

  // Emergency toggles: we use the same edit dict but execute as a
  // single-key save so the rest of the dirty edits don't ride along.
  const onToggleEmergency = (key, currentValue) => {
    setConfirm({
      key,
      label:
        EMERGENCY_SWITCHES.find((s) => s.k === key)?.label || key,
      nextValue: !currentValue,
    });
  };

  const confirmEmergency = async () => {
    if (!confirm) return;
    setSaving(true);
    setError(null);
    try {
      await adminConfig.patchConfig({ [confirm.key]: confirm.nextValue });
      await refresh();
    } catch (err) {
      setError(err.message || "Failed to toggle switch.");
    } finally {
      setSaving(false);
      setConfirm(null);
    }
  };

  const renderValueCell = (item, prefix) => {
    const key = fullKey(prefix, item.k);
    const cur = edited[key] !== undefined ? edited[key] : server[key];

    if (item.type === "bool") {
      return (
        <div style={{ display: "flex", gap: 4 }}>
          <button
            type="button"
            className={`chip ${cur === true ? "solid" : ""}`}
            style={{ cursor: "pointer" }}
            onClick={() => setEditedKey(key, true)}
          >
            true
          </button>
          <button
            type="button"
            className={`chip ${cur === false ? "solid" : ""}`}
            style={{ cursor: "pointer" }}
            onClick={() => setEditedKey(key, false)}
          >
            false
          </button>
        </div>
      );
    }

    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          value={cur ?? ""}
          onChange={(e) => {
            const next = parseValue(e.target.value, item.type);
            if (next !== null) setEditedKey(key, next);
            // If the value is invalid we still keep the edited string
            // so the input doesn't snap back; but we record `null` so
            // the dirty diff doesn't include garbage.
            else setEditedKey(key, e.target.value);
          }}
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
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
            {item.suffix}
          </span>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      {/* ---------- emergency switches (top bill) ---------- */}
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
          {EMERGENCY_SWITCHES.map((s) => {
            const on = Boolean(server[s.k]);
            return (
              <div
                key={s.k}
                style={{
                  padding: "12px 14px",
                  border: "1px solid var(--ink)",
                  background: on ? "#f3d6d0" : "var(--paper-2)",
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
                <button
                  type="button"
                  onClick={() => onToggleEmergency(s.k, on)}
                  style={{
                    width: 56,
                    height: 28,
                    border: "1px solid var(--ink)",
                    background: on ? "var(--bad)" : "var(--paper-3)",
                    position: "relative",
                    cursor: "pointer",
                    flexShrink: 0,
                  }}
                  aria-label={`toggle ${s.k}`}
                >
                  <div
                    style={{
                      position: "absolute",
                      top: 1,
                      left: on ? 30 : 1,
                      width: 24,
                      height: 24,
                      background: on ? "var(--paper)" : "var(--ink)",
                      transition: "left 120ms",
                    }}
                  />
                </button>
              </div>
            );
          })}
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

      {loading && (
        <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
          loading config…
        </div>
      )}

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
            {sec.items.map((item, i) => {
              const key = fullKey(sec.prefix, item.k);
              const dirty = edited[key] !== undefined;
              return (
                <div
                  key={item.k}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1.5fr 200px 1fr",
                    gap: 16,
                    alignItems: "center",
                    padding: "12px 16px",
                    borderTop: i ? "1px solid var(--rule)" : "none",
                    background: dirty ? "var(--banana-soft)" : "transparent",
                  }}
                >
                  <div className="mono" style={{ fontSize: 12 }}>
                    <span style={{ color: "var(--ink-3)" }}>
                      {sec.prefix === "—" ? "" : `${sec.prefix}.`}
                    </span>
                    <span style={{ fontWeight: 700 }}>{item.k}</span>
                  </div>
                  <div>{renderValueCell(item, sec.prefix)}</div>
                  {item.hint && (
                    <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                      {item.hint}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {/* ---------- save bar ---------- */}
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
          <span style={{ color: "var(--banana)" }}>
            {dirtyKeys.length} change{dirtyKeys.length === 1 ? "" : "s"}
          </span>{" "}
          · weights sum {weightSum.toFixed(2)}{" "}
          <span style={{ color: weightsOk ? "var(--ok)" : "var(--bad)" }}>
            {weightsOk ? "✓" : "✗"}
          </span>
          {savedAt && dirtyKeys.length === 0 && (
            <span style={{ marginLeft: 12, color: "var(--paper-2)" }}>
              · saved at {savedAt.toLocaleTimeString()}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn ghost"
            onClick={onDiscard}
            disabled={saving || dirtyKeys.length === 0}
            style={{ color: "var(--paper)", borderColor: "var(--paper-2)" }}
          >
            Discard
          </button>
          <button
            type="button"
            className="btn primary shadowed"
            onClick={onSave}
            disabled={saving || dirtyKeys.length === 0 || !weightsOk}
          >
            {saving ? "Saving…" : "Save & reload"}
          </button>
        </div>
      </div>

      {/* ---------- emergency confirm modal ---------- */}
      {confirm && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
          onClick={() => !saving && setConfirm(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: 460,
              border: "2px solid var(--ink)",
              background: "var(--paper)",
              boxShadow: "5px 5px 0 var(--ink)",
              padding: 22,
            }}
          >
            <div className="display" style={{ fontSize: 22, fontWeight: 800 }}>
              {confirm.nextValue ? "Activate" : "Deactivate"} switch?
            </div>
            <div
              className="mono"
              style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 8 }}
            >
              {confirm.label}
            </div>
            <div
              style={{
                display: "flex",
                gap: 8,
                justifyContent: "flex-end",
                marginTop: 18,
              }}
            >
              <button
                type="button"
                className="btn"
                disabled={saving}
                onClick={() => setConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn ink shadowed"
                disabled={saving}
                onClick={confirmEmergency}
              >
                {saving ? "Saving…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
