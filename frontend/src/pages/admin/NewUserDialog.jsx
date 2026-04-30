import { useState } from "react";
import * as adminUsers from "../../api/admin/users.js";

// "New user" modal. Lives in its own file so the parent UsersTab can
// stay readable. The shape of the form mirrors UserCreateRequest in the
// backend — leave the override fields blank to fall back to tier defaults.
export default function NewUserDialog({ open, onClose, onCreated }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({
    username: "",
    password: "",
    role: "user",
    tier: "free",
    display_name: "",
    override_soft_quota: "",
    override_hard_quota: "",
  });

  if (!open) return null;

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body = {
        username: form.username.trim(),
        password: form.password,
        role: form.role,
        tier: form.tier,
        display_name: form.display_name.trim() || null,
        override_soft_quota:
          form.override_soft_quota === ""
            ? null
            : Number(form.override_soft_quota),
        override_hard_quota:
          form.override_hard_quota === ""
            ? null
            : Number(form.override_hard_quota),
      };
      const detail = await adminUsers.createUser(body);
      onCreated?.(detail);
      onClose?.();
    } catch (err) {
      setError(err.message || "Create failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        style={{
          width: 460,
          background: "var(--paper)",
          border: "1px solid var(--ink)",
          boxShadow: "5px 5px 0 var(--ink)",
          padding: 20,
        }}
      >
        <div
          className="mono caps"
          style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}
        >
          ADMIN · NEW USER
        </div>
        <div
          className="display"
          style={{
            fontSize: 28,
            fontWeight: 800,
            letterSpacing: "-0.02em",
            margin: "8px 0 16px",
          }}
        >
          Create account
        </div>

        <Field label="username">
          <input
            className="inp"
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            required
            pattern="[A-Za-z0-9_.-]{3,64}"
            title="3–64 chars, letters / numbers / _ . -"
            style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
        </Field>

        <Field label="password (≥ 8 chars)">
          <input
            className="inp"
            type="password"
            minLength={8}
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
            style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
        </Field>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field label="role">
            <select
              className="inp"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </Field>
          <Field label="tier">
            <select
              className="inp"
              value={form.tier}
              onChange={(e) => setForm({ ...form, tier: e.target.value })}
              style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="vip">vip</option>
              <option value="premium">premium</option>
              <option value="standard">standard</option>
              <option value="free">free</option>
            </select>
          </Field>
        </div>

        <Field label="display name (optional)">
          <input
            className="inp"
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
        </Field>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field label="override soft_quota (optional)">
            <input
              className="inp"
              inputMode="numeric"
              value={form.override_soft_quota}
              onChange={(e) =>
                setForm({ ...form, override_soft_quota: e.target.value })
              }
              style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
          </Field>
          <Field label="override hard_quota (optional)">
            <input
              className="inp"
              inputMode="numeric"
              value={form.override_hard_quota}
              onChange={(e) =>
                setForm({ ...form, override_hard_quota: e.target.value })
              }
              style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
          </Field>
        </div>

        {error && (
          <div
            style={{
              marginTop: 10,
              padding: 8,
              fontSize: 12,
              color: "var(--bad)",
              border: "1px solid var(--bad)",
            }}
          >
            {error}
          </div>
        )}

        <div
          style={{
            marginTop: 16,
            display: "flex",
            gap: 8,
            justifyContent: "flex-end",
          }}
        >
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            cancel
          </button>
          <button type="submit" className="btn primary shadowed" disabled={busy}>
            {busy ? "creating…" : "Create user"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
        {label}
      </div>
      <div style={{ marginTop: 4 }}>{children}</div>
    </div>
  );
}
