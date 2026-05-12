// Editable preview of the system prompt template used by the fallback
// path. Only renders when the user has expanded the toggle on
// MaskMethodBar. Default state: locked + showing the default template.
// "unlock to edit" makes the textarea editable; "reset to default"
// returns to the default text.
//
// The template is *not* stored on the server — at submit time the
// frontend resolves it against the user's prompt, and that resolved
// string is what hits ``payload.prompt`` on /api/jobs. Backend has no
// awareness of the template.

import { useRef } from "react";

export default function FallbackTemplateBlock({
  value,
  defaultValue,
  locked,
  modified,
  onChange,
  onLock,
  onUnlock,
  onReset,
}) {
  const taRef = useRef(null);
  const displayValue = value ?? defaultValue;
  return (
    <div className="me-fallback-template" data-testid="me-fallback-template">
      <div className="me-fallback-template__header">
        <div className="me-fallback-template__title">
          <span className="me-fallback-template__title-text">
            system prompt template
          </span>
          {modified && (
            <span className="me-fallback-template__modified" data-testid="me-template-modified">
              modified
            </span>
          )}
        </div>
        <div className="me-fallback-template__hint">
          auto-prepended to your prompt
          <br />
          at submit time
        </div>
      </div>
      <textarea
        ref={taRef}
        className={`me-fallback-template__ta ${locked ? "me-fallback-template__ta--locked" : ""}`}
        readOnly={locked}
        rows={7}
        value={displayValue}
        onChange={(e) => onChange?.(e.target.value)}
        data-testid="me-template-textarea"
      />
      <div className="me-fallback-template__footer">
        <div className="me-fallback-template__actions">
          {locked ? (
            <button
              type="button"
              className="btn sm ghost"
              style={{ height: 22, fontSize: 10, padding: "0 8px" }}
              onClick={() => onUnlock?.()}
              data-testid="me-template-unlock"
            >
              unlock to edit
            </button>
          ) : (
            <>
              <button
                type="button"
                className="btn sm ghost"
                style={{ height: 22, fontSize: 10, padding: "0 8px" }}
                onClick={() => onLock?.()}
              >
                lock
              </button>
              {modified && (
                <button
                  type="button"
                  className="btn sm ghost"
                  style={{ height: 22, fontSize: 10, padding: "0 8px" }}
                  onClick={() => onReset?.()}
                  data-testid="me-template-reset"
                >
                  reset to default
                </button>
              )}
            </>
          )}
        </div>
        {!locked && (
          <span className="me-fallback-template__warn">
            ⚠ editing this may cause the<br />model to behave unpredictably
          </span>
        )}
      </div>
    </div>
  );
}
