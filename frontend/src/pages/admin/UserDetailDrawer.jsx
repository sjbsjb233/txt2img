import { useEffect, useMemo, useState } from "react";
import Icon from "../../components/Icon.jsx";
import * as adminUsers from "../../api/admin/users.js";
import { Hair, TierPill } from "./atoms.jsx";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatRelative(iso) {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  if (ms < 60_000) return "just now";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}

function statusChip(status, todayCount, soft, hard) {
  if (status === "disabled") {
    return <span className="chip" style={{ background: "var(--paper-3)" }}>DISABLED</span>;
  }
  if (status === "deleted") {
    return <span className="chip" style={{ background: "var(--bad)", color: "var(--paper)" }}>DELETED</span>;
  }
  if (todayCount >= hard) {
    return <span className="chip" style={{ background: "var(--bad)", color: "var(--paper)" }}>HARD LIMIT</span>;
  }
  if (todayCount >= soft) {
    return <span className="chip warn">SOFT BREACH</span>;
  }
  return <span className="chip ok">ACTIVE</span>;
}

// ---------------------------------------------------------------------------
// 30-day usage strip with hover tooltip
// ---------------------------------------------------------------------------

function DailyUsageStrip({ usagePoints, maxUsage }) {
  const [hover, setHover] = useState(null); // { i, x, y, parentWidth }
  const total = usagePoints.reduce((a, p) => a + (p.jobs_count || 0), 0);
  const peak = usagePoints.length
    ? usagePoints.reduce((m, p) => (p.jobs_count > m ? p.jobs_count : m), 0)
    : 0;
  const peakIdx = peak > 0 ? usagePoints.findIndex((p) => p.jobs_count === peak) : -1;

  function handleEnter(i, e) {
    const rect = e.currentTarget.getBoundingClientRect();
    const parent = e.currentTarget.parentElement.parentElement.getBoundingClientRect();
    setHover({
      i,
      x: rect.left - parent.left + rect.width / 2,
      y: rect.top - parent.top,
      parentWidth: parent.width,
    });
  }

  return (
    <div
      style={{ position: "relative", marginTop: 10 }}
      onMouseLeave={() => setHover(null)}
    >
      <div
        data-testid="user-daily-strip"
        style={{
          height: 80,
          display: "flex",
          alignItems: "flex-end",
          gap: 2,
        }}
      >
        {usagePoints.map((p, i) => {
          const h = Math.max(2, (p.jobs_count / maxUsage) * 100);
          const success = p.jobs_count ? p.success_count / p.jobs_count : 1;
          const tone =
            p.jobs_count === 0
              ? "var(--paper-3)"
              : success >= 0.9
                ? "var(--ink-2)"
                : "var(--warn)";
          const isHovered = hover?.i === i;
          const ariaLabel =
            `${p.date}, ${p.jobs_count} jobs, ` +
            `${p.success_count} ok, ${Math.max(0, p.jobs_count - p.success_count)} fail`;
          return (
            <div
              key={p.date}
              data-testid={`user-daily-bar-${i}`}
              tabIndex={0}
              role="img"
              aria-label={ariaLabel}
              onMouseEnter={(e) => handleEnter(i, e)}
              onFocus={(e) => handleEnter(i, e)}
              onBlur={() => setHover(null)}
              style={{
                flex: 1,
                height: `${h}%`,
                background: isHovered ? "var(--banana-deep)" : tone,
                cursor: "crosshair",
                transition: "background 0.1s ease",
                outline: "none",
              }}
            />
          );
        })}
      </div>
      {hover && (() => {
        const p = usagePoints[hover.i];
        const jobs = p.jobs_count || 0;
        const ok = p.success_count || 0;
        const fail = Math.max(0, jobs - ok);
        const successPct = jobs ? (ok / jobs) * 100 : 0;
        const sharePct = total > 0 ? (jobs / total) * 100 : 0;
        const tipWidth = 230;
        const maxLeft = Math.max(0, hover.parentWidth - tipWidth);
        const left = Math.max(0, Math.min(hover.x - tipWidth / 2, maxLeft));
        return (
          <div
            data-testid="user-daily-tooltip"
            style={{
              position: "absolute",
              left,
              top: Math.max(-92, hover.y - 100),
              width: tipWidth,
              padding: "8px 10px",
              background: "var(--ink)",
              color: "var(--paper)",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              lineHeight: 1.4,
              border: "1px solid var(--ink)",
              boxShadow: "0 4px 0 0 var(--banana)",
              pointerEvents: "none",
              zIndex: 5,
            }}
          >
            <div style={{ fontWeight: 700, letterSpacing: "0.04em" }}>
              {p.date}
            </div>
            <div style={{ marginTop: 4 }}>
              jobs · <span style={{ color: "var(--banana)", fontWeight: 700 }}>{jobs}</span>
              <span style={{ opacity: 0.6 }}> / peak {peak}</span>
            </div>
            <div>
              ok · {ok} · fail · {fail}
            </div>
            <div>
              success · {jobs ? `${successPct.toFixed(1)}%` : "—"}
            </div>
            <div>
              share · {sharePct.toFixed(1)}% of {total} · 30d
            </div>
            {hover.i === peakIdx && peak > 0 && (
              <div style={{ color: "var(--banana)", marginTop: 2 }}>← peak day</div>
            )}
          </div>
        );
      })()}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

/**
 * Focused user drawer — shown below the table when a row is clicked.
 *
 * Owns:
 *  - inline edits (display_name, tier, override quotas)
 *  - the disable / enable / reset-password / impersonate actions
 *  - the 30-day usage strip + top models / providers
 *
 * Talks straight to api/admin/users.js. The parent passes `userId`
 * and a callback that fires after any write so the table can refresh.
 */
export default function UserDetailDrawer({ userId, onClose, onChanged, currentAdminId }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Editable form state — initialised from the detail response and
  // re-synced whenever the userId changes or the parent triggers a refresh.
  const [form, setForm] = useState({
    display_name: "",
    tier: "free",
    override_soft_quota: "",
    override_hard_quota: "",
  });
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState(null);

  // Reset-password local state
  const [pwOpen, setPwOpen] = useState(false);
  const [newPw, setNewPw] = useState("");

  // Bottom-of-drawer admin job listing (lazy)
  const [jobs, setJobs] = useState(null);
  const [jobsLoading, setJobsLoading] = useState(false);

  const refreshDetail = async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await adminUsers.getUser(userId);
      setDetail(d);
      setForm({
        display_name: d.display_name || "",
        tier: d.tier,
        override_soft_quota:
          d.override_soft_quota == null ? "" : String(d.override_soft_quota),
        override_hard_quota:
          d.override_hard_quota == null ? "" : String(d.override_hard_quota),
      });
    } catch (err) {
      setError(err.message || "Failed to load user.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!userId) return;
    void refreshDetail();
    setJobs(null);
    setPwOpen(false);
    setNewPw("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const usagePoints = detail?.daily_usage || [];
  const maxUsage = useMemo(
    () => Math.max(1, ...usagePoints.map((p) => p.jobs_count)),
    [usagePoints],
  );

  if (!userId) return null;

  // --- handlers --------------------------------------------------------

  const saveProfile = async () => {
    if (!detail) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const patch = {};
      if ((detail.display_name || "") !== form.display_name) {
        patch.display_name = form.display_name || null;
      }
      if (detail.tier !== form.tier) {
        patch.tier = form.tier;
      }
      const overrideSoft =
        form.override_soft_quota === "" ? null : Number(form.override_soft_quota);
      const overrideHard =
        form.override_hard_quota === "" ? null : Number(form.override_hard_quota);
      if (overrideSoft !== detail.override_soft_quota) {
        patch.override_soft_quota = overrideSoft;
      }
      if (overrideHard !== detail.override_hard_quota) {
        patch.override_hard_quota = overrideHard;
      }
      if (Object.keys(patch).length === 0) {
        setSaveMsg("Nothing changed.");
        return;
      }
      const updated = await adminUsers.patchUser(userId, patch);
      setDetail(updated);
      setSaveMsg("Saved.");
      onChanged?.();
    } catch (err) {
      setSaveMsg(err.message || "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const toggleStatus = async () => {
    if (!detail) return;
    try {
      if (detail.status === "disabled") {
        await adminUsers.enableUser(userId);
      } else {
        await adminUsers.disableUser(userId);
      }
      await refreshDetail();
      onChanged?.();
    } catch (err) {
      setSaveMsg(err.message || "Status change failed.");
    }
  };

  const resetPassword = async () => {
    if (!newPw || newPw.length < 8) {
      setSaveMsg("New password must be at least 8 characters.");
      return;
    }
    try {
      await adminUsers.resetPassword(userId, newPw);
      setPwOpen(false);
      setNewPw("");
      setSaveMsg("Password reset.");
    } catch (err) {
      setSaveMsg(err.message || "Reset failed.");
    }
  };

  const impersonate = async () => {
    try {
      const res = await adminUsers.impersonate(userId);
      // Open the new window first so the popup blocker treats it as
      // a direct user gesture; then patch in the impersonation hash.
      const url = `${window.location.origin}/login#impersonate=${encodeURIComponent(
        res.access_token,
      )}`;
      const w = window.open(url, "_blank", "noopener,noreferrer");
      if (!w) {
        setSaveMsg("Popup blocked — allow popups for this site to impersonate.");
      } else {
        setSaveMsg(`Impersonation window opened (${res.expires_in_seconds}s).`);
      }
    } catch (err) {
      setSaveMsg(err.message || "Impersonate failed.");
    }
  };

  const softDelete = async () => {
    if (!detail) return;
    const ok = window.confirm(
      `Soft-delete @${detail.username}? The username will be released for reuse.`,
    );
    if (!ok) return;
    try {
      await adminUsers.deleteUser(userId);
      onChanged?.();
      onClose?.();
    } catch (err) {
      setSaveMsg(err.message || "Delete failed.");
    }
  };

  const loadJobs = async () => {
    setJobsLoading(true);
    try {
      const res = await adminUsers.listUserJobs(userId, { pageSize: 50 });
      setJobs(res.items);
    } catch (err) {
      setSaveMsg(err.message || "Could not load jobs.");
    } finally {
      setJobsLoading(false);
    }
  };

  const isSelf = currentAdminId && detail && detail.id === currentAdminId;

  return (
    <div style={{ marginTop: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 10,
        }}
      >
        <div
          className="mono caps"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            letterSpacing: "0.16em",
          }}
        >
          FOCUSED · @{detail?.username || "…"}
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn sm ghost" onClick={onClose}>
          close
        </button>
      </div>
      <Hair thick />

      {loading && (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 12 }}>
          loading…
        </div>
      )}
      {error && !loading && (
        <div
          style={{
            marginTop: 12,
            padding: 10,
            border: "1px solid var(--bad)",
            color: "var(--bad)",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      {detail && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.4fr 1fr",
            gap: 16,
            marginTop: 12,
          }}
        >
          {/* ---------- left: profile + edits ---------- */}
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
                  {detail.username}
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}
                >
                  {detail.id} · created {formatDate(detail.created_at)}
                </div>
                <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
                  <TierPill tier={detail.tier} />
                  {statusChip(
                    detail.status,
                    detail.today_count,
                    detail.soft_quota_effective,
                    detail.hard_quota_effective,
                  )}
                  {detail.role === "admin" && (
                    <span className="chip solid">ADMIN</span>
                  )}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => setPwOpen((v) => !v)}
                >
                  Reset password
                </button>
                <button
                  type="button"
                  className="btn sm"
                  onClick={toggleStatus}
                  disabled={isSelf || detail.status === "deleted"}
                  title={isSelf ? "Cannot disable yourself." : undefined}
                >
                  {detail.status === "disabled" ? "Enable" : "Disable"}
                </button>
                <button
                  type="button"
                  className="btn sm ink"
                  onClick={impersonate}
                  disabled={isSelf || detail.status !== "active"}
                  title={
                    isSelf
                      ? "Cannot impersonate yourself."
                      : detail.status !== "active"
                        ? "Only active users can be impersonated."
                        : undefined
                  }
                >
                  Impersonate · 30m
                </button>
                <button
                  type="button"
                  className="btn sm"
                  onClick={softDelete}
                  disabled={isSelf || detail.status === "deleted"}
                  title={isSelf ? "Cannot delete yourself." : undefined}
                  style={{ color: "var(--bad)" }}
                >
                  Soft delete
                </button>
              </div>
            </div>

            {pwOpen && (
              <div
                style={{
                  marginTop: 14,
                  padding: 10,
                  border: "1px dashed var(--ink-4)",
                  display: "flex",
                  gap: 8,
                  alignItems: "center",
                }}
              >
                <input
                  type="password"
                  className="inp"
                  placeholder="new password (≥ 8 chars)"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                  style={{ flex: 1, fontFamily: "var(--font-mono)", fontSize: 12 }}
                />
                <button className="btn sm primary" onClick={resetPassword}>
                  apply
                </button>
                <button className="btn sm ghost" onClick={() => setPwOpen(false)}>
                  cancel
                </button>
              </div>
            )}

            <div
              style={{
                marginTop: 18,
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
              }}
            >
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                  display name
                </div>
                <input
                  className="inp"
                  value={form.display_name}
                  onChange={(e) =>
                    setForm({ ...form, display_name: e.target.value })
                  }
                  style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
                  placeholder="—"
                />
              </div>
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                  tier
                </div>
                <select
                  className="inp"
                  value={form.tier}
                  onChange={(e) => setForm({ ...form, tier: e.target.value })}
                  style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
                >
                  <option value="vip">vip</option>
                  <option value="premium">premium</option>
                  <option value="standard">standard</option>
                  <option value="free">free</option>
                </select>
              </div>
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                  override soft_quota
                </div>
                <input
                  className="inp"
                  inputMode="numeric"
                  value={form.override_soft_quota}
                  onChange={(e) =>
                    setForm({ ...form, override_soft_quota: e.target.value })
                  }
                  style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
                  placeholder={`— (use tier default ${detail.soft_quota_effective})`}
                />
              </div>
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                  override hard_quota
                </div>
                <input
                  className="inp"
                  inputMode="numeric"
                  value={form.override_hard_quota}
                  onChange={(e) =>
                    setForm({ ...form, override_hard_quota: e.target.value })
                  }
                  style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
                  placeholder={`— (use tier default ${detail.hard_quota_effective})`}
                />
              </div>
            </div>

            <div
              style={{
                marginTop: 14,
                display: "flex",
                gap: 10,
                alignItems: "center",
              }}
            >
              <button
                className="btn primary shadowed"
                onClick={saveProfile}
                disabled={saving || detail.status === "deleted"}
              >
                {saving ? "saving…" : "Save changes"}
              </button>
              {saveMsg && (
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  {saveMsg}
                </span>
              )}
            </div>
          </div>

          {/* ---------- right: usage / top models / sessions ---------- */}
          <div
            style={{ border: "1px solid var(--ink)", background: "#fffdf7", padding: 18 }}
          >
            <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              30 DAYS · JOBS PER DAY
            </div>
            <DailyUsageStrip
              usagePoints={usagePoints}
              maxUsage={maxUsage}
            />
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
              <span>
                {detail.jobs_30d_total} jobs · {detail.jobs_30d_success} ok ·{" "}
                {detail.jobs_30d_failed} fail
              </span>
              <span>today · {detail.today_count}</span>
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
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
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
                  {(detail.top_models || []).length === 0 && (
                    <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                      no usage yet
                    </div>
                  )}
                  {detail.top_models?.map((it, i) => (
                    <div key={it.key} className="mono" style={{ fontSize: 11 }}>
                      {i + 1} · {it.key} · {it.count}
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
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
                  {(detail.top_providers || []).length === 0 && (
                    <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                      no usage yet
                    </div>
                  )}
                  {detail.top_providers?.map((it, i) => (
                    <div key={it.key} className="mono" style={{ fontSize: 11 }}>
                      {i + 1} · {it.key} · {it.count}
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <Hair />
            <div
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 10 }}
            >
              active sessions · {detail.active_session_count}
            </div>
            <div
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}
            >
              last login · {formatRelative(detail.last_login_at)}
            </div>
          </div>
        </div>
      )}

      {/* ---------- jobs panel (lazy) ---------- */}
      {detail && (
        <div style={{ marginTop: 16 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 8,
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
            >
              RECENT JOBS · admin view
            </div>
            <div style={{ flex: 1 }} />
            <button className="btn sm" onClick={loadJobs} disabled={jobsLoading}>
              <Icon name="refresh" size={12} />
              {jobsLoading ? "loading…" : jobs ? "reload" : "load"}
            </button>
          </div>
          {jobs && (
            <div style={{ border: "1px solid var(--ink)", background: "#fffdf7" }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "200px 220px 110px 140px 100px 80px 80px",
                  gap: 12,
                  padding: "8px 14px",
                  background: "var(--ink)",
                  color: "var(--paper)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                  fontWeight: 700,
                }}
              >
                <div>HASH</div>
                <div>MODEL</div>
                <div>STATUS</div>
                <div>PROVIDER</div>
                <div>RETRIES</div>
                <div>COST</div>
                <div>WHEN</div>
              </div>
              {jobs.length === 0 && (
                <div
                  className="mono"
                  style={{
                    padding: 14,
                    fontSize: 11,
                    color: "var(--ink-3)",
                    fontStyle: "italic",
                  }}
                >
                  no jobs yet
                </div>
              )}
              {jobs.map((j, i) => (
                <div
                  key={j.hash_id}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "200px 220px 110px 140px 100px 80px 80px",
                    gap: 12,
                    padding: "8px 14px",
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    borderTop: i ? "1px solid var(--rule)" : "none",
                    alignItems: "center",
                  }}
                >
                  <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {j.hash_id}
                  </div>
                  <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {j.model}
                  </div>
                  <div style={{ fontWeight: 700 }}>{j.status}</div>
                  <div>{j.provider_used || "—"}</div>
                  <div>{j.retries}</div>
                  <div>¥{(j.cost_cny || 0).toFixed(3)}</div>
                  <div style={{ color: "var(--ink-3)" }}>
                    {formatRelative(j.created_at)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
