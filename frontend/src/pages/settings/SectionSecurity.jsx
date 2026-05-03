// Section 07 — Security. Self-service password change, active auth
// sessions list, and a placeholder 2FA row.

import { useCallback, useEffect, useState } from "react";
import * as meApi from "../../api/me.js";
import {
  Chip,
  Icon,
  SectionCard,
  SectionHead,
  SettingRow,
  Toggle,
} from "./components.jsx";

function relativeTime(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function shortUserAgent(ua) {
  if (!ua) return "Unknown device";
  // Cheap sniff — we don't pull in ua-parser-js for two text labels.
  let device = "Unknown";
  if (/Edg\//.test(ua)) device = "Edge";
  else if (/Chrome\//.test(ua)) device = "Chrome";
  else if (/Firefox\//.test(ua)) device = "Firefox";
  else if (/Safari\//.test(ua)) device = "Safari";
  let os = "";
  if (/Windows/.test(ua)) os = "Windows";
  else if (/Macintosh|Mac OS X/.test(ua)) os = "macOS";
  else if (/Linux/.test(ua)) os = "Linux";
  else if (/Android/.test(ua)) os = "Android";
  else if (/iPhone|iPad|iOS/.test(ua)) os = "iOS";
  return os ? `${device} on ${os}` : device;
}

function PasswordCard({ daysSinceChange }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState(null);

  const validate = () => {
    if (!current) return "Enter your current password.";
    if (next.length < 10) return "New password must be at least 10 characters.";
    const classes = [/[A-Za-z]/.test(next), /[0-9]/.test(next), /[^A-Za-z0-9]/.test(next)].filter(
      Boolean
    ).length;
    if (classes < 2) return "Mix at least two of letters, digits, and symbols.";
    if (next === current) return "New password cannot be the same as the current one.";
    if (next !== confirm) return "Confirmation does not match.";
    return null;
  };

  const onSubmit = async () => {
    setError(null);
    setSuccess(false);
    const v = validate();
    if (v) {
      setError(v);
      return;
    }
    setBusy(true);
    try {
      await meApi.changePassword({
        current_password: current,
        new_password: next,
      });
      setSuccess(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err?.message || "Could not update password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        padding: "16px 20px",
        borderTop: "1px solid var(--rule)",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span className="label">Change password</span>
        <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          {daysSinceChange == null
            ? "Never changed"
            : `Last changed ${daysSinceChange}d ago`}
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 10,
          alignItems: "end",
        }}
      >
        <label style={{ gridColumn: "1 / span 2", fontSize: 11, color: "var(--ink-3)" }}>
          Current password
          <input
            className="inp"
            type={show ? "text" : "password"}
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            placeholder="••••••••••"
            autoComplete="current-password"
            style={{ marginTop: 4, height: 32 }}
          />
        </label>
        <label style={{ fontSize: 11, color: "var(--ink-3)" }}>
          New password
          <input
            className="inp"
            type={show ? "text" : "password"}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            placeholder="At least 10 characters"
            autoComplete="new-password"
            style={{ marginTop: 4, height: 32 }}
          />
        </label>
        <label style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Confirm new
          <input
            className="inp"
            type={show ? "text" : "password"}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            style={{ marginTop: 4, height: 32 }}
          />
        </label>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          fontSize: 11,
        }}
      >
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={show}
            onChange={(e) => setShow(e.target.checked)}
          />
          Show passwords
        </label>
        {error && (
          <span style={{ color: "var(--bad)", flex: 1 }}>{error}</span>
        )}
        {success && (
          <span style={{ color: "var(--ok)", flex: 1 }}>
            Password updated. Other sessions were signed out.
          </span>
        )}
        <button
          className="btn primary sm"
          onClick={onSubmit}
          disabled={busy}
          style={{ marginLeft: "auto" }}
        >
          <Icon name="check" size={11} />
          {busy ? "Updating…" : "Update password"}
        </button>
      </div>
    </div>
  );
}

function SessionsCard({ sessions, currentId, onRevoke, onRevokeOthers, busy }) {
  if (!sessions) {
    return (
      <div
        style={{
          padding: "12px 20px",
          fontSize: 12,
          color: "var(--ink-3)",
          borderTop: "1px solid var(--rule)",
        }}
      >
        Loading sessions…
      </div>
    );
  }

  const others = sessions.filter((s) => !s.is_current);

  return (
    <div style={{ borderTop: "1px solid var(--rule)" }}>
      <div
        style={{
          padding: "14px 20px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span className="label">Active sessions</span>
        <Chip tone="solid">{sessions.length} active</Chip>
      </div>

      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          borderTop: "1px solid var(--rule)",
        }}
      >
        {sessions.map((s) => (
          <li
            key={s.id}
            style={{
              padding: "10px 20px",
              display: "flex",
              alignItems: "center",
              gap: 12,
              borderBottom: "1px solid var(--rule)",
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                background: s.is_current ? "var(--banana)" : "var(--ink-3)",
                flexShrink: 0,
                marginTop: 4,
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                {shortUserAgent(s.user_agent)}
                {s.is_current && (
                  <span
                    className="chip banana"
                    style={{ marginLeft: 8, padding: "1px 6px", fontSize: 10 }}
                  >
                    NOW
                  </span>
                )}
              </div>
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)" }}
              >
                {s.ip_hint || "ip unknown"} · started {relativeTime(s.created_at)}
                {!s.is_current && ` · last seen ${relativeTime(s.last_active_at)}`}
              </div>
            </div>
            {!s.is_current && (
              <button
                className="btn sm"
                onClick={() => onRevoke(s.id)}
                disabled={busy}
              >
                Revoke
              </button>
            )}
          </li>
        ))}
      </ul>
      <div
        style={{
          padding: "12px 20px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Sign out everywhere — invalidates all tokens; you'll be returned to the login screen.
        </span>
        <button
          className="btn sm"
          onClick={onRevokeOthers}
          disabled={busy || others.length === 0}
        >
          Sign out other devices
        </button>
      </div>
    </div>
  );
}

export default function SectionSecurity({ me, onMeChanged }) {
  const [sessions, setSessions] = useState(null);
  const [currentId, setCurrentId] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await meApi.listSessions();
      setSessions(data?.sessions || []);
      setCurrentId(data?.current_session_id || null);
    } catch {
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onRevoke = async (id) => {
    if (!window.confirm("Revoke this session?")) return;
    setBusy(true);
    try {
      await meApi.revokeSession(id);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const onRevokeOthers = async () => {
    if (!window.confirm("Sign out every other device?")) return;
    setBusy(true);
    try {
      await meApi.revokeOtherSessions();
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const daysSinceChange = (() => {
    if (!me?.password_changed_at) return null;
    const t = Date.parse(me.password_changed_at);
    if (Number.isNaN(t)) return null;
    return Math.max(0, Math.floor((Date.now() - t) / (1000 * 60 * 60 * 24)));
  })();

  return (
    <SectionCard>
      <SectionHead
        number="07"
        title="Security"
        right={
          sessions ? (
            <Chip>{sessions.length} active session{sessions.length === 1 ? "" : "s"}</Chip>
          ) : null
        }
      />

      <PasswordCard daysSinceChange={daysSinceChange} />

      <SessionsCard
        sessions={sessions}
        currentId={currentId}
        onRevoke={onRevoke}
        onRevokeOthers={onRevokeOthers}
        busy={busy}
      />

      <SettingRow
        label="Two-factor authentication"
        hint="TOTP via authenticator app once available."
        help="2FA support is on the roadmap; this row is reserved so you'll know where to enable it when it ships."
      >
        <Chip tone="warn">Coming soon</Chip>
        <Toggle checked={false} onChange={() => {}} disabled aria-label="2FA" />
      </SettingRow>
    </SectionCard>
  );
}
