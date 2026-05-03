// Admin "Approvals" tab — per-user requests waiting for admin action.
//
// Today this surface owns one kind of request: account-deletion
// submissions from /settings → Danger zone. The page lists them in a
// reading-order feed and offers approve / reject controls inline.
// Rejecting and approving both take an optional admin note that's
// persisted on the AccountDeletionRequest row for the audit trail.

import { useCallback, useEffect, useMemo, useState } from "react";
import Icon from "../../components/Icon.jsx";
import { StatusDot, TierPill } from "./atoms.jsx";
import * as approvalsApi from "../../api/admin/approvals.js";

const STATUS_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "all", label: "All" },
];

function formatTs(value) {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
  } catch {
    return String(value);
  }
}

function relative(value) {
  if (!value) return "";
  const t = Date.parse(value);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function statusTone(status) {
  if (status === "pending") return "warn";
  if (status === "approved") return "bad";
  if (status === "rejected") return "muted";
  if (status === "withdrawn") return "muted";
  return "info";
}

function StatusChip({ status }) {
  return (
    <span
      className="mono caps"
      style={{
        fontSize: 10,
        letterSpacing: "0.14em",
        padding: "2px 8px",
        border: "1px solid var(--ink)",
        background:
          status === "pending"
            ? "var(--banana)"
            : status === "approved"
            ? "var(--bad)"
            : "var(--paper-2)",
        color:
          status === "approved" ? "var(--paper)" : "var(--ink)",
      }}
    >
      {status}
    </span>
  );
}

function DecisionDialog({
  open,
  kind,
  busy,
  error,
  user,
  onClose,
  onConfirm,
}) {
  const [note, setNote] = useState("");
  useEffect(() => {
    if (open) setNote("");
  }, [open]);
  if (!open) return null;
  const isApprove = kind === "approve";

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
        <div>
          <div
            className="mono caps"
            style={{
              fontSize: 10,
              color: "var(--ink-3)",
              letterSpacing: "0.18em",
            }}
          >
            APPROVALS · DELETION
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
            {isApprove
              ? `Approve deletion of @${user.username}?`
              : `Reject deletion of @${user.username}?`}
          </div>
        </div>

        {isApprove && (
          <div
            style={{
              padding: 12,
              border: "1px solid var(--bad)",
              background: "rgba(155,45,32,0.08)",
              fontSize: 12,
              color: "var(--ink-2)",
              lineHeight: 1.55,
            }}
          >
            Approving will <strong>disable</strong> the user immediately and
            sign every active session out. After 7 days the cleanup job will
            soft-delete the account, and after 30 days the data is purged.
          </div>
        )}

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
            Admin note (optional, ≤ 500 chars)
          </span>
          <textarea
            className="inp"
            rows={3}
            maxLength={500}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={
              isApprove
                ? "Reason for approval; visible in the audit log."
                : "Reason for rejection; shown internally only."
            }
            style={{ resize: "vertical" }}
          />
        </label>

        {error && (
          <div
            style={{
              padding: "8px 10px",
              border: "1px solid var(--bad)",
              color: "var(--bad)",
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
          <button className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn shadowed"
            style={
              isApprove
                ? {
                    background: "var(--bad)",
                    color: "var(--paper)",
                  }
                : { background: "var(--ink)", color: "var(--paper)" }
            }
            onClick={() => onConfirm(note)}
            disabled={busy}
          >
            {busy
              ? isApprove
                ? "Approving…"
                : "Rejecting…"
              : isApprove
              ? "Approve & disable"
              : "Reject"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RequestRow({ row, onApprove, onReject }) {
  const isPending = row.status === "pending";
  return (
    <div
      style={{
        background: "var(--card)",
        border: "1px solid var(--ink)",
        boxShadow: "3px 3px 0 var(--ink)",
        padding: 16,
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: 14,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 14, fontWeight: 700 }}>
            {row.user.display_name || row.user.username}
          </span>
          <span
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-3)" }}
          >
            @{row.user.username}
          </span>
          <TierPill tier={row.user.tier} />
          <StatusChip status={row.status} />
        </div>

        <div
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--ink-3)",
            marginTop: 4,
            display: "flex",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <span>id: {row.user.id}</span>
          <span>email: {row.user.email || "—"}</span>
          <span>requested: {formatTs(row.requested_at)} ({relative(row.requested_at)})</span>
        </div>

        <div
          style={{
            marginTop: 12,
            border: "1px solid var(--rule)",
            background: "var(--paper-2)",
            padding: "10px 12px",
            fontSize: 12,
            color: "var(--ink-2)",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 4 }}
          >
            User reason
          </div>
          {row.reason ? row.reason : <em style={{ color: "var(--ink-4)" }}>(no reason given)</em>}
        </div>

        {row.admin_note && (
          <div
            style={{
              marginTop: 8,
              fontSize: 11,
              color: "var(--ink-2)",
              fontStyle: "italic",
            }}
          >
            <span className="mono caps" style={{ marginRight: 6 }}>
              Admin note:
            </span>
            {row.admin_note}
          </div>
        )}

        {!isPending && row.resolved_at && (
          <div
            className="mono"
            style={{
              fontSize: 10,
              color: "var(--ink-3)",
              marginTop: 6,
            }}
          >
            resolved {formatTs(row.resolved_at)} by {row.resolved_by || "—"}
          </div>
        )}
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          alignSelf: "flex-start",
        }}
      >
        {isPending ? (
          <>
            <button
              type="button"
              className="btn sm shadowed"
              style={{ background: "var(--bad)", color: "var(--paper)" }}
              onClick={() => onApprove(row)}
            >
              <Icon name="check" size={11} /> Approve
            </button>
            <button
              type="button"
              className="btn sm"
              onClick={() => onReject(row)}
            >
              <Icon name="close" size={11} /> Reject
            </button>
          </>
        ) : (
          <span
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)" }}
          >
            no action
          </span>
        )}
      </div>
    </div>
  );
}

export default function ApprovalsTab() {
  const [status, setStatus] = useState("pending");
  const [list, setList] = useState({ items: [], total_pending: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [dialog, setDialog] = useState(null); // { kind: "approve"|"reject", row }
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await approvalsApi.listDeletionRequests({ status });
      setList(data);
    } catch (err) {
      setError(err?.message || "Could not load requests.");
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onApprove = (row) => setDialog({ kind: "approve", row });
  const onReject = (row) => setDialog({ kind: "reject", row });

  const onConfirm = async (note) => {
    if (!dialog) return;
    setDialogBusy(true);
    setDialogError(null);
    try {
      const fn =
        dialog.kind === "approve"
          ? approvalsApi.approveDeletion
          : approvalsApi.rejectDeletion;
      await fn(dialog.row.id, { admin_note: note || null });
      setDialog(null);
      await refresh();
    } catch (err) {
      setDialogError(err?.message || "Decision failed.");
    } finally {
      setDialogBusy(false);
    }
  };

  const counters = useMemo(
    () => ({ pending: list.total_pending }),
    [list.total_pending]
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
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
            APPROVALS · /api/admin/approvals
          </div>
          <h2
            className="display"
            style={{
              fontSize: 32,
              fontWeight: 800,
              letterSpacing: "-0.02em",
              margin: "8px 0 0",
              lineHeight: 1.1,
            }}
          >
            User requests
          </h2>
          <div
            style={{
              fontSize: 13,
              color: "var(--ink-3)",
              marginTop: 6,
              fontStyle: "italic",
            }}
          >
            Account deletions submitted from the user-facing settings page.
          </div>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <StatusDot
            tone={counters.pending > 0 ? "warn" : "ok"}
            label={`${counters.pending} pending`}
          />
          <button className="btn sm" onClick={refresh}>
            <Icon name="refresh" size={11} /> Refresh
          </button>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          borderBottom: "1px solid var(--rule)",
        }}
      >
        {STATUS_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            data-active={opt.value === status ? "true" : "false"}
            onClick={() => setStatus(opt.value)}
            style={{
              padding: "8px 12px",
              border: "none",
              background: "transparent",
              fontSize: 12,
              fontWeight: opt.value === status ? 700 : 500,
              color:
                opt.value === status ? "var(--ink)" : "var(--ink-3)",
              borderBottom:
                opt.value === status
                  ? "2px solid var(--ink)"
                  : "2px solid transparent",
              marginBottom: -1,
              cursor: "pointer",
            }}
          >
            {opt.label}
            {opt.value === "pending" && counters.pending > 0 && (
              <span
                className="mono"
                style={{
                  marginLeft: 6,
                  padding: "1px 6px",
                  background:
                    opt.value === status ? "var(--ink)" : "var(--paper-3)",
                  color:
                    opt.value === status ? "var(--banana)" : "var(--ink-3)",
                  fontSize: 10,
                }}
              >
                {counters.pending}
              </span>
            )}
          </button>
        ))}
      </div>

      {loading && (
        <div style={{ fontSize: 13, color: "var(--ink-3)" }}>Loading…</div>
      )}

      {error && (
        <div
          style={{
            padding: "10px 12px",
            border: "1px solid var(--bad)",
            color: "var(--bad)",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      {!loading && !error && list.items.length === 0 && (
        <div
          style={{
            padding: 32,
            border: "1px dashed var(--ink-3)",
            color: "var(--ink-3)",
            fontSize: 13,
            textAlign: "center",
          }}
        >
          No {status === "all" ? "" : status} deletion requests.
        </div>
      )}

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        {list.items.map((row) => (
          <RequestRow
            key={row.id}
            row={row}
            onApprove={onApprove}
            onReject={onReject}
          />
        ))}
      </div>

      <DecisionDialog
        open={Boolean(dialog)}
        kind={dialog?.kind}
        user={dialog?.row?.user || { username: "" }}
        busy={dialogBusy}
        error={dialogError}
        onClose={() => {
          if (!dialogBusy) {
            setDialog(null);
            setDialogError(null);
          }
        }}
        onConfirm={onConfirm}
      />
    </div>
  );
}
