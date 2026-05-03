// Section 09 — Danger zone. Sign out (current device), and self-service
// account deletion request. The deletion modal explains the timeline
// inline so the user can't miss it.

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as meApi from "../../api/me.js";
import { logout as logoutFlow } from "../../store/auth.js";
import {
  Chip,
  Icon,
  SectionCard,
  SectionHead,
  SettingRow,
} from "./components.jsx";

function DeletionModal({ busy, onClose, onSubmit }) {
  // No ``open`` prop: parent decides whether to mount us at all, and
  // a ``key`` prop on the parent's render guarantees a fresh component
  // instance per opening. That sidesteps the useEffect-on-open race
  // where resetting state could clobber a checkbox the user has just
  // ticked, and it also lets us drop the ``useEffect`` entirely.
  const [reason, setReason] = useState("");
  const [agreed, setAgreed] = useState(false);

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
        padding: 20,
      }}
      onClick={onClose}
    >
      {/* Match the visual language of the other admin/settings modals
          (NewUserDialog, BulkPatchDialog): warm paper background +
          1px ink border + the brutalist 5px offset shadow, no inner
          card backdrop. */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(520px, 100%)",
          background: "var(--paper)",
          border: "1px solid var(--ink)",
          boxShadow: "5px 5px 0 var(--ink)",
          padding: 24,
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
          }}
        >
          <div>
            <div
              className="mono caps"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                letterSpacing: "0.18em",
              }}
            >
              ACCOUNT · DELETION
            </div>
            <div
              className="display"
              style={{
                fontSize: 26,
                fontWeight: 800,
                letterSpacing: "-0.02em",
                margin: "8px 0 0",
                lineHeight: 1.1,
              }}
            >
              Request account deletion
            </div>
          </div>
          <button
            type="button"
            className="btn sm"
            onClick={onClose}
            aria-label="Close"
            disabled={busy}
          >
            <Icon name="close" size={11} />
          </button>
        </div>

        <div
          style={{
            background: "var(--paper-2)",
            border: "1px solid var(--ink)",
            padding: 12,
          }}
        >
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-3)",
              letterSpacing: "0.18em",
              marginBottom: 6,
            }}
          >
            Timeline
          </div>
          <ol
            style={{
              fontSize: 12,
              lineHeight: 1.6,
              color: "var(--ink-2)",
              paddingLeft: 18,
              margin: 0,
            }}
          >
            <li>An admin reviews and approves the request.</li>
            <li>Once approved, your account is disabled and you're signed out everywhere.</li>
            <li>After 7 days the account is soft-deleted; data is purged after 30 days.</li>
          </ol>
        </div>

        <label
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <span
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)" }}
          >
            Reason (optional)
          </span>
          <textarea
            className="inp"
            rows={3}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Helps admins decide; visible only to admins."
            style={{ resize: "vertical" }}
          />
        </label>

        <label
          style={{
            fontSize: 12,
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            color: "var(--ink-2)",
            padding: 10,
            border: "1px solid var(--ink)",
            background: "var(--paper-2)",
          }}
        >
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            style={{ marginTop: 2 }}
          />
          <span>
            I understand my jobs and archive will be soft-deleted after admin
            approval, and that I'll be signed out everywhere.
          </span>
        </label>

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn shadowed"
            style={{
              background: "var(--bad)",
              color: "var(--paper)",
              borderColor: "var(--ink)",
            }}
            onClick={() => onSubmit(reason)}
            disabled={busy || !agreed}
          >
            {busy ? "Submitting…" : "Request deletion"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SectionDanger() {
  const navigate = useNavigate();
  const [pending, setPending] = useState(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refresh = async () => {
    try {
      const data = await meApi.getDeletionRequest();
      setPending(data && data.status === "pending" ? data : null);
    } catch {
      setPending(null);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const onSignOut = async () => {
    if (!window.confirm("Sign out of this device?")) return;
    await logoutFlow();
    navigate("/login", { replace: true });
  };

  const onSubmit = async (reason) => {
    setError(null);
    setBusy(true);
    try {
      await meApi.requestDeletion({ reason: reason || null });
      setOpen(false);
      await refresh();
    } catch (err) {
      if (err?.code === "DELETION_REQUEST_PENDING") {
        await refresh();
        setOpen(false);
      } else {
        setError(err?.message || "Could not submit request.");
      }
    } finally {
      setBusy(false);
    }
  };

  const onWithdraw = async () => {
    if (!window.confirm("Withdraw the pending deletion request?")) return;
    setBusy(true);
    try {
      await meApi.withdrawDeletion();
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard danger>
      <SectionHead
        number="09"
        title="Danger zone"
        right={<Chip tone="bad">Irreversible</Chip>}
      />

      <SettingRow
        label="Sign out"
        hint="Signs you out of this device only."
      >
        <button className="btn sm" onClick={onSignOut}>
          <Icon name="logout" size={11} /> Sign out
        </button>
      </SettingRow>

      {pending ? (
        <div style={{ padding: "12px 20px", borderTop: "1px solid var(--rule)" }}>
          <div className="danger-banner">
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                Deletion request pending
              </div>
              <div style={{ fontSize: 11, color: "var(--ink-2)" }}>
                Submitted {new Date(pending.requested_at).toLocaleString()}.
                An admin will review shortly.
              </div>
            </div>
            <button className="btn sm" onClick={onWithdraw} disabled={busy}>
              Withdraw
            </button>
          </div>
        </div>
      ) : (
        <SettingRow
          label="Request account deletion"
          hint="Admins review requests; account is disabled after approval and purged after 30 days."
          danger
        >
          <button
            className="btn sm"
            style={{
              background: "var(--bad)",
              color: "var(--paper)",
              borderColor: "var(--bad)",
            }}
            onClick={() => setOpen(true)}
          >
            Request deletion
          </button>
        </SettingRow>
      )}

      {error && (
        <div
          style={{
            padding: "0 20px 12px",
            fontSize: 11,
            color: "var(--bad)",
          }}
        >
          {error}
        </div>
      )}

      {open && (
        <DeletionModal
          busy={busy}
          onClose={() => setOpen(false)}
          onSubmit={onSubmit}
        />
      )}
    </SectionCard>
  );
}
