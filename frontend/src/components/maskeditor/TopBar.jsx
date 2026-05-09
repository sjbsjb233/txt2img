import MEIcon from "./MEIcon.jsx";
import DraftToast from "../DraftToast.jsx";

const STATUS_TEXT = {
  idle: "ready · ⌘↵",
  submitting: "submitting",
  running: "running",
  done: "done",
  error: "error",
  empty: "mask too small",
};
const STATUS_COLOR = {
  idle: "var(--ink)",
  submitting: "var(--info)",
  running: "var(--info)",
  done: "var(--ok)",
  error: "var(--bad)",
  empty: "var(--bad)",
};

export default function TopBar({
  sourceLabel,
  mode = "inpaint",
  model,
  status = "idle",
  canSubmit = true,
  onBack,
  onSubmit,
  draftToast = null,
}) {
  return (
    <div className="me-topbar" data-testid="me-topbar">
      <button
        className="me-topbar__back"
        onClick={onBack}
        title="Back to archive"
        data-testid="me-back"
      >
        <MEIcon name="back" size={18} />
      </button>
      <div className="me-topbar__crumbs">
        <span className="me-topbar__crumb">archive / {sourceLabel} /</span>
        <span className="me-topbar__title">edit with mask</span>
        <span className="chip">{mode}</span>
      </div>
      <div className="me-topbar__cell" style={{ flexDirection: "column", alignItems: "flex-end", gap: 0, lineHeight: 1.2 }}>
        <span className="me-topbar__cell-label">model</span>
        <span className="me-topbar__cell-value">{model || "—"}</span>
      </div>
      <div className="me-topbar__cell" style={{ flexDirection: "column", alignItems: "flex-end", gap: 0, lineHeight: 1.2 }}>
        <span className="me-topbar__cell-label">status</span>
        <span
          className="me-topbar__cell-value"
          style={{ color: STATUS_COLOR[status] || "var(--ink)" }}
          data-testid="me-status"
        >
          {STATUS_TEXT[status] || status}
        </span>
      </div>
      <DraftToast value={draftToast} />
      <button
        className="me-topbar__submit"
        disabled={!canSubmit}
        onClick={onSubmit}
        data-testid="me-submit"
      >
        <MEIcon name="submit" size={14} />
        submit
        <span className="kbd" style={{ marginLeft: 6, background: "rgba(0,0,0,0.1)", boxShadow: "none" }}>⌘↵</span>
      </button>
    </div>
  );
}
