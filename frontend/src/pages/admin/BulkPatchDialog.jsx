import { useState } from "react";
import * as adminUsers from "../../api/admin/users.js";

// Apply one patch to many users. The backend whitelists `tier` /
// `status` / override quotas — anything else 422s. We mirror that
// whitelist in the form so admins don't accidentally try to bulk-set
// passwords or display names (those are per-user secrets).
export default function BulkPatchDialog({ open, ids, onClose, onApplied }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [field, setField] = useState("tier");
  const [tierValue, setTierValue] = useState("free");
  const [statusValue, setStatusValue] = useState("active");
  const [softValue, setSoftValue] = useState("");
  const [hardValue, setHardValue] = useState("");

  if (!open) return null;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const patch = {};
      if (field === "tier") patch.tier = tierValue;
      if (field === "status") patch.status = statusValue;
      if (field === "override_soft_quota") {
        patch.override_soft_quota =
          softValue === "" ? null : Number(softValue);
      }
      if (field === "override_hard_quota") {
        patch.override_hard_quota =
          hardValue === "" ? null : Number(hardValue);
      }
      const res = await adminUsers.bulkPatch(ids, patch);
      onApplied?.(res);
      onClose?.();
    } catch (err) {
      setError(err.message || "Bulk patch failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        style={{
          width: 420,
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
          ADMIN · BULK PATCH
        </div>
        <div
          className="display"
          style={{
            fontSize: 24,
            fontWeight: 800,
            letterSpacing: "-0.02em",
            margin: "8px 0 12px",
          }}
        >
          Apply to {ids.length} user{ids.length === 1 ? "" : "s"}
        </div>

        <div style={{ marginBottom: 10 }}>
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            field
          </div>
          <select
            className="inp"
            value={field}
            onChange={(e) => setField(e.target.value)}
            style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
          >
            <option value="tier">tier</option>
            <option value="status">status</option>
            <option value="override_soft_quota">override_soft_quota</option>
            <option value="override_hard_quota">override_hard_quota</option>
          </select>
        </div>

        <div style={{ marginBottom: 10 }}>
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            new value
          </div>
          {field === "tier" && (
            <select
              className="inp"
              value={tierValue}
              onChange={(e) => setTierValue(e.target.value)}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="vip">vip</option>
              <option value="premium">premium</option>
              <option value="standard">standard</option>
              <option value="free">free</option>
            </select>
          )}
          {field === "status" && (
            <select
              className="inp"
              value={statusValue}
              onChange={(e) => setStatusValue(e.target.value)}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="active">active</option>
              <option value="disabled">disabled</option>
            </select>
          )}
          {field === "override_soft_quota" && (
            <input
              className="inp"
              inputMode="numeric"
              placeholder="leave empty to clear"
              value={softValue}
              onChange={(e) => setSoftValue(e.target.value)}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
          )}
          {field === "override_hard_quota" && (
            <input
              className="inp"
              inputMode="numeric"
              placeholder="leave empty to clear"
              value={hardValue}
              onChange={(e) => setHardValue(e.target.value)}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
          )}
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

        <div style={{ marginTop: 16, display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            cancel
          </button>
          <button type="submit" className="btn primary shadowed" disabled={busy}>
            {busy ? "applying…" : "Apply"}
          </button>
        </div>
      </form>
    </div>
  );
}
