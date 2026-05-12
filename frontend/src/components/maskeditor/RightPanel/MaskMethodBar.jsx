// Mask-method indicator strip — sits at the top of the prompt panel.
// Shows whether the current model is using native or fallback masking.
// In fallback mode there's a small ▸ show template / ▾ hide template
// trigger that toggles the FallbackTemplateBlock below.

export default function MaskMethodBar({ method, expanded, onToggle }) {
  const isFallback = method === "fallback";
  return (
    <div className="me-mask-method-bar" data-testid="me-mask-method-bar" data-method={method || "unknown"}>
      <span
        className={`me-mask-method-bar__dot me-mask-method-bar__dot--${
          isFallback ? "fallback" : "native"
        }`}
      />
      <span className="me-mask-method-bar__label" data-testid="me-mask-method-label">
        {isFallback ? "mask · fallback (described in prompt)" : "mask · native"}
        {!isFallback && <span className="me-mask-method-bar__check"> ✓</span>}
      </span>
      {isFallback && (
        <button
          type="button"
          className="me-mask-method-bar__toggle"
          onClick={onToggle}
          data-testid="me-template-toggle"
        >
          {expanded ? "▾ hide template" : "▸ show template"}
        </button>
      )}
    </div>
  );
}
