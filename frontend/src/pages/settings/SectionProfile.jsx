// Section 01 — Profile. Display name + email + the "member since" footer.
//
// We keep `display_name` / `email` edits in local state until the user
// hits Save. That mirrors the rest of the page (which uses the
// preferences store's draft / save model) instead of submitting on
// every keystroke.

import { useEffect, useState } from "react";
import * as meApi from "../../api/me.js";
import { updateUser as updateAuthUser } from "../../store/auth.js";
import { Chip, Icon, SectionCard, SectionHead, SettingRow } from "./components.jsx";

function formatAbsolute(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d
      .toISOString()
      .replace("T", " ")
      .replace(/\.\d+Z$/, " UTC");
  } catch {
    return "—";
  }
}

function dayDelta(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const ms = Date.now() - d.getTime();
  return Math.max(0, Math.floor(ms / (1000 * 60 * 60 * 24)));
}

export default function SectionProfile({ me, onMeChanged }) {
  const initialDisplay = me?.display_name || "";
  const initialEmail = me?.email || "";
  const [displayName, setDisplayName] = useState(initialDisplay);
  const [email, setEmail] = useState(initialEmail);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDisplayName(me?.display_name || "");
    setEmail(me?.email || "");
  }, [me?.display_name, me?.email]);

  const dirty =
    (displayName.trim() !== (initialDisplay || "").trim()) ||
    (email.trim() !== (initialEmail || "").trim());

  const initials = (displayName || me?.username || "??")
    .slice(0, 2)
    .toUpperCase();

  const onSave = async () => {
    setError(null);
    setSaving(true);
    try {
      const payload = {};
      if (displayName.trim() !== (initialDisplay || "").trim()) {
        payload.display_name = displayName.trim();
      }
      if (email.trim() !== (initialEmail || "").trim()) {
        payload.email = email.trim();
      }
      const updated = await meApi.patchMe(payload);
      // Push the new display_name / email into the auth store so the
      // sidebar avatar / name and the dashboard greeting update without
      // requiring a page refresh.
      updateAuthUser({
        display_name: updated.display_name,
        email: updated.email,
      });
      onMeChanged && onMeChanged(updated);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <SectionCard>
      <SectionHead
        number="01"
        title="Profile"
        right={<Chip tone="solid">Identity</Chip>}
      />

      {/* Avatar block */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "16px 20px",
          borderBottom: "1px solid var(--rule)",
        }}
      >
        <div
          style={{
            width: 56,
            height: 56,
            background: "var(--ink)",
            color: "var(--banana)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "var(--font-mono)",
            fontWeight: 700,
            fontSize: 18,
            border: "1px solid var(--ink)",
            flexShrink: 0,
          }}
        >
          {initials}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 22,
              fontWeight: 500,
              lineHeight: 1.1,
              marginBottom: 4,
            }}
          >
            {displayName || me?.username || "—"}
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-3)" }}
          >
            @{me?.username} · {me?.id}
          </div>
        </div>
        <Chip tone="solid">Member</Chip>
      </div>

      <SettingRow
        label="Display name"
        hint="Shown in the sidebar and on shared sessions."
      >
        <input
          className="inp"
          style={{ width: 220, height: 32 }}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          maxLength={50}
          placeholder="Your name"
        />
      </SettingRow>

      <SettingRow
        label="Email (optional)"
        hint="Used for password recovery and digest notifications."
        help="We never display your email publicly. Leave blank to remove the address from your account."
      >
        <input
          className="inp"
          type="email"
          style={{ width: 260, height: 32 }}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="not set"
          maxLength={200}
        />
      </SettingRow>

      <SettingRow
        label="Member since"
        hint={
          me?.last_login_at
            ? `Last login ${formatAbsolute(me.last_login_at)}.`
            : "First login pending."
        }
      >
        <span className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
          {me?.created_at ? formatAbsolute(me.created_at).slice(0, 10) : "—"}
        </span>
      </SettingRow>

      {(dirty || error) && (
        <div
          style={{
            padding: "10px 20px",
            borderTop: "1px solid var(--rule)",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 8,
          }}
        >
          {error && (
            <span style={{ fontSize: 12, color: "var(--bad)", marginRight: "auto" }}>
              {error.message || "Could not save changes."}
            </span>
          )}
          {dirty && (
            <button
              className="btn primary sm"
              onClick={onSave}
              disabled={saving}
            >
              <Icon name="check" size={12} />
              {saving ? "Saving…" : "Save profile"}
            </button>
          )}
        </div>
      )}
    </SectionCard>
  );
}
