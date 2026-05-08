import MEIcon from "./MEIcon.jsx";

const ERROR_COPY = {
  UPSTREAM_BLOCKED: {
    title: "upstream blocked",
    body: "OpenAI's content policy rejected the prompt. The mask and references look fine — try rephrasing the change you want to make (avoid named people, brands, violent content).",
  },
  INVALID_MASK_FORMAT: {
    title: "mask format invalid",
    body: "The mask isn't a valid PNG with an alpha channel.",
  },
  INVALID_MASK_DIMS: {
    title: "mask size mismatch",
    body: "The mask dimensions don't match the source image.",
  },
  RATE_LIMITED: {
    title: "too many requests",
    body: "Too many requests right now — wait a moment and retry.",
  },
  QUOTA_EXCEEDED: {
    title: "quota exhausted",
    body: "Today's quota is used up. It refreshes tomorrow.",
  },
  UPSTREAM_TIMEOUT: {
    title: "upstream timeout",
    body: "Upstream took too long to respond. You can retry.",
  },
  PARENT_JOB_NOT_FOUND: {
    title: "source job not found",
    body: "The source job has been removed. Pick another image to edit.",
  },
};

export default function ErrorModal({ code, message, onEditPrompt, onRetry, onCopy, onClose }) {
  const copy = ERROR_COPY[code] || {
    title: "something went wrong",
    body: message || "an unexpected error occurred",
  };
  return (
    <div className="me-error-modal-backdrop" data-testid="me-error-modal">
      <div className="me-error-modal">
        <div className="me-error-modal__title">
          <MEIcon name="warn" size={18} stroke="var(--bad)" />
          <span>{copy.title}</span>
          <span className="me-error-modal__chip">{code || "ERROR"}</span>
        </div>
        <div className="me-error-modal__body">{copy.body}</div>
        {message && message !== copy.body && (
          <div style={{ fontSize: 11, color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>
            {message}
          </div>
        )}
        <div className="me-error-modal__actions">
          <button className="btn primary" onClick={onEditPrompt} data-testid="me-error-edit-prompt">edit prompt</button>
          <button className="btn" onClick={onRetry} data-testid="me-error-retry">retry</button>
          <button
            className="btn ghost"
            onClick={onCopy}
            data-testid="me-error-copy"
            style={{ marginLeft: "auto" }}
          >
            copy error code
          </button>
          <button
            className="btn ghost"
            onClick={onClose}
            aria-label="dismiss"
            data-testid="me-error-close"
            style={{ padding: "0 10px" }}
          >
            <MEIcon name="close" size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
