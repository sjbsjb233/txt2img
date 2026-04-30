import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as adminCleanup from "../../api/admin/cleanup.js";
import { Hair } from "./atoms.jsx";

const STATUS_OPTIONS = ["SUCCEEDED", "FAILED", "CANCELLED", "DELETED"];

function humanBytes(n) {
  if (n == null) return "—";
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let val = n;
  let i = 0;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i += 1;
  }
  return i === 0 ? `${Math.round(val)} ${units[i]}` : `${val.toFixed(1)} ${units[i]}`;
}

function formatCutoff(cutoff) {
  if (!cutoff) return "—";
  try {
    return new Date(cutoff).toISOString().slice(0, 10);
  } catch {
    return cutoff;
  }
}

function describeRule(rule) {
  if (rule.kind === "older_than_days") {
    if (rule.statuses?.length) {
      return `${rule.statuses.join("/")} older than ${rule.days}d`;
    }
    return `Older than ${rule.days}d`;
  }
  return `${(rule.statuses || []).join("/")} · any age`;
}

export default function CleanupTab() {
  // Server data
  const [suggestions, setSuggestions] = useState([]);
  const [diskUsage, setDiskUsage] = useState({});
  const [refreshedAt, setRefreshedAt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Custom rule local state
  const [customDays, setCustomDays] = useState("30");
  const [customStatuses, setCustomStatuses] = useState(
    () => new Set(["SUCCEEDED", "FAILED"]),
  );
  const [exemptStarred, setExemptStarred] = useState(true);

  // Async dry-run / execute output
  const [dryRunResult, setDryRunResult] = useState(null);
  const [activeTask, setActiveTask] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState(null); // {label, rules}
  const pollTimer = useRef(null);

  const loadSuggestions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminCleanup.getSuggestions();
      setSuggestions(res.suggestions || []);
      setDiskUsage(res.disk_usage || {});
      setRefreshedAt(res.refreshed_at);
    } catch (err) {
      setError(err.message || "Failed to load cleanup suggestions.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSuggestions();
  }, [loadSuggestions]);

  // Poll the active task until it's done, then refresh suggestions.
  useEffect(() => {
    if (!activeTask?.task_id) return;
    if (activeTask.status === "done" || activeTask.status === "failed") {
      void loadSuggestions();
      return;
    }
    pollTimer.current = setTimeout(async () => {
      try {
        const next = await adminCleanup.getCleanupTask(activeTask.task_id);
        setActiveTask(next);
      } catch (err) {
        setActiveTask((prev) =>
          prev
            ? { ...prev, status: "failed", error: err.message || "poll failed" }
            : prev,
        );
      }
    }, 600);
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [activeTask, loadSuggestions]);

  const customRule = useMemo(() => {
    const days = Number.parseInt(customDays, 10);
    const statuses = Array.from(customStatuses);
    if (statuses.length === 0) return null;
    if (Number.isNaN(days) || days < 0) return null;
    return [
      {
        kind: "older_than_days",
        days,
        statuses,
      },
    ];
  }, [customDays, customStatuses]);

  const toggleStatus = (status) => {
    setCustomStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  };

  const runDryRun = async (rules) => {
    setBusy(true);
    setError(null);
    setDryRunResult(null);
    try {
      const res = await adminCleanup.dryRunCleanup({
        rules,
        exemptStarred,
      });
      setDryRunResult({ ...res, rules });
    } catch (err) {
      setError(err.message || "Dry-run failed.");
    } finally {
      setBusy(false);
    }
  };

  const runExecute = async (rules) => {
    setBusy(true);
    setError(null);
    try {
      const res = await adminCleanup.executeCleanup({
        rules,
        exemptStarred,
      });
      setActiveTask(res);
      setDryRunResult(null);
    } catch (err) {
      setError(err.message || "Cleanup failed to start.");
    } finally {
      setBusy(false);
      setConfirmTarget(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div
        style={{
          display: "flex",
          gap: 14,
          alignItems: "flex-start",
          justifyContent: "space-between",
        }}
      >
        <div>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            /api/admin/cleanup
          </div>
          <div
            className="display"
            style={{ fontSize: 32, fontWeight: 800, letterSpacing: "-0.025em", marginTop: 6 }}
          >
            Free up{" "}
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>disk.</span>
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
            DB rows flag DELETED first; then async rm -rf data/jobs/&lt;hash&gt;.
            SSE task_deleted broadcasts.
          </div>
        </div>
        <div
          style={{
            minWidth: 240,
            padding: 14,
            border: "1px solid var(--ink)",
            background: "#fffdf7",
          }}
        >
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            DATA/JOBS DISK
          </div>
          <div
            className="ticker"
            style={{
              fontSize: 36,
              fontWeight: 900,
              letterSpacing: "-0.04em",
              lineHeight: 1,
              marginTop: 6,
            }}
          >
            {humanBytes(diskUsage.data_jobs_bytes ?? 0).split(" ")[0]}{" "}
            <span className="mono" style={{ fontSize: 14, color: "var(--ink-3)" }}>
              {humanBytes(diskUsage.data_jobs_bytes ?? 0).split(" ")[1]}
            </span>
          </div>
          <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 8 }}>
            {diskUsage.job_count ?? 0} jobs · refreshed{" "}
            {refreshedAt
              ? new Date(refreshedAt).toLocaleString()
              : "never"}
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

      {/* ---------- active task progress ---------- */}
      {activeTask && (
        <div
          style={{
            border: "2px solid var(--ink)",
            background: "var(--banana-soft)",
            padding: "12px 16px",
            display: "flex",
            alignItems: "center",
            gap: 14,
          }}
        >
          <div style={{ flex: 1 }}>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              CLEANUP TASK · {activeTask.task_id}
            </div>
            <div className="mono" style={{ fontSize: 13, fontWeight: 700, marginTop: 2 }}>
              {activeTask.status.toUpperCase()} · {activeTask.processed_jobs}/
              {activeTask.total_jobs} jobs · {humanBytes(activeTask.deleted_bytes)} freed
            </div>
            {activeTask.error && (
              <div className="mono" style={{ fontSize: 11, color: "var(--bad)", marginTop: 4 }}>
                {activeTask.error}
              </div>
            )}
          </div>
          {(activeTask.status === "done" || activeTask.status === "failed") && (
            <button
              type="button"
              className="btn sm"
              onClick={() => setActiveTask(null)}
            >
              Dismiss
            </button>
          )}
        </div>
      )}

      {/* ---------- dry-run result preview ---------- */}
      {dryRunResult && (
        <div
          style={{
            border: "1px solid var(--ink)",
            background: "var(--paper-2)",
            padding: "12px 16px",
            display: "flex",
            alignItems: "center",
            gap: 14,
          }}
        >
          <div style={{ flex: 1 }}>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              DRY-RUN PREVIEW
            </div>
            <div className="mono" style={{ fontSize: 13, fontWeight: 700, marginTop: 2 }}>
              would delete {dryRunResult.job_count} jobs ·{" "}
              {dryRunResult.image_count} images · frees {dryRunResult.disk_human}
            </div>
          </div>
          <button
            type="button"
            className="btn sm"
            onClick={() => setDryRunResult(null)}
          >
            Dismiss
          </button>
          <button
            type="button"
            className="btn sm ink"
            disabled={busy}
            onClick={() =>
              setConfirmTarget({
                label: "this dry-run",
                rules: dryRunResult.rules,
              })
            }
          >
            Run for real
          </button>
        </div>
      )}

      {/* ---------- suggestions ---------- */}
      <div>
        <div
          className="mono caps"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            letterSpacing: "0.16em",
            marginBottom: 10,
          }}
        >
          SUGGESTIONS
        </div>
        <Hair thick />
        <div
          style={{
            marginTop: 12,
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {loading && (
            <div
              className="mono"
              style={{ padding: 12, color: "var(--ink-3)", fontStyle: "italic" }}
            >
              loading suggestions…
            </div>
          )}
          {!loading &&
            suggestions.map((s) => (
              <div
                key={`${s.rule.kind}-${s.rule.days}-${(s.rule.statuses || []).join(",")}`}
                style={{
                  border: "1px solid var(--ink)",
                  background: "#fffdf7",
                  padding: "14px 18px",
                  display: "grid",
                  gridTemplateColumns: "minmax(220px, 1.4fr) 1fr 1fr 1fr 200px",
                  gap: 16,
                  alignItems: "center",
                  position: "relative",
                }}
              >
                <div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>
                    {s.period_label}
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}
                  >
                    cutoff · {formatCutoff(s.cutoff)}
                  </div>
                </div>
                <div>
                  <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                    JOBS
                  </div>
                  <div
                    className="ticker"
                    style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.02em" }}
                  >
                    {s.job_count.toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                    IMAGES
                  </div>
                  <div
                    className="ticker"
                    style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.02em" }}
                  >
                    {s.image_count.toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                    FREES
                  </div>
                  <div
                    className="ticker"
                    style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.02em" }}
                  >
                    {s.disk_human}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button
                    type="button"
                    className="btn sm"
                    disabled={busy || s.job_count === 0}
                    onClick={() => runDryRun([s.rule])}
                  >
                    Dry-run
                  </button>
                  <button
                    type="button"
                    className="btn sm ink"
                    disabled={busy || s.job_count === 0}
                    onClick={() =>
                      setConfirmTarget({
                        label: s.period_label,
                        rules: [s.rule],
                      })
                    }
                  >
                    Run cleanup
                  </button>
                </div>
              </div>
            ))}
        </div>
      </div>

      {/* ---------- custom rule ---------- */}
      <div>
        <div
          className="mono caps"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            letterSpacing: "0.16em",
            marginBottom: 10,
          }}
        >
          CUSTOM RULES
        </div>
        <Hair thick />
        <div
          style={{
            marginTop: 12,
            padding: 16,
            border: "1px solid var(--ink)",
            background: "#fffdf7",
          }}
        >
          <div
            style={{
              display: "flex",
              gap: 10,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
              older_than_days ·
            </span>
            <input
              className="inp"
              value={customDays}
              onChange={(e) => setCustomDays(e.target.value)}
              style={{ width: 100, fontFamily: "var(--font-mono)" }}
            />
            <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
              · statuses ·
            </span>
            {STATUS_OPTIONS.map((s) => {
              const active = customStatuses.has(s);
              return (
                <button
                  key={s}
                  type="button"
                  className={`chip ${active ? "solid" : ""}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => toggleStatus(s)}
                >
                  {s}
                </button>
              );
            })}
          </div>
          <div
            style={{
              marginTop: 14,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <label
              className="mono"
              style={{
                fontSize: 12,
                color: "var(--ink-2)",
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <input
                type="checkbox"
                checked={exemptStarred}
                onChange={(e) => setExemptStarred(e.target.checked)}
              />
              exempt starred=true images
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className="btn"
                disabled={busy || customRule == null}
                onClick={() => runDryRun(customRule)}
              >
                Dry-run
              </button>
              <button
                type="button"
                className="btn ink shadowed"
                disabled={busy || customRule == null}
                onClick={() =>
                  setConfirmTarget({
                    label: "custom rule",
                    rules: customRule,
                  })
                }
              >
                Execute cleanup
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ---------- confirm modal ---------- */}
      {confirmTarget && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.35)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
          onClick={() => setConfirmTarget(null)}
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
              Confirm cleanup
            </div>
            <div
              className="mono"
              style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 8 }}
            >
              You are about to permanently delete jobs matching:
            </div>
            <ul
              className="mono"
              style={{ fontSize: 12, marginTop: 6, paddingLeft: 18 }}
            >
              {confirmTarget.rules.map((r, i) => (
                <li key={i}>{describeRule(r)}</li>
              ))}
            </ul>
            <div
              style={{
                fontSize: 12,
                color: "var(--ink-3)",
                marginTop: 8,
                fontStyle: "italic",
                fontFamily: "var(--font-display)",
              }}
            >
              {exemptStarred
                ? "Starred images will be skipped."
                : "Even starred images will be deleted."}
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
                onClick={() => setConfirmTarget(null)}
                disabled={busy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn ink shadowed"
                disabled={busy}
                onClick={() => runExecute(confirmTarget.rules)}
              >
                {busy ? "Starting…" : "Yes, run cleanup"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
