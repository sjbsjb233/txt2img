// `‹ N / M ›` pager. The arrows close all other loaded pages and jump
// to the target page (scrollTop = 0). Disabled state is reflected via
// `data-disabled` so the test suite can poll it cheaply.

export default function PaginationPager({
  current,
  total,
  onPrev,
  onNext,
}) {
  if (!total) return null;
  const prevDisabled = current <= 1;
  const nextDisabled = current >= total;
  return (
    <div
      data-testid="archive-pager"
      role="group"
      aria-label="archive pages"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        height: 32,
        padding: "0 4px",
        border: "1px solid var(--ink)",
        background: "var(--card, #fffdf7)",
      }}
    >
      <PagerButton
        testid="pager-prev"
        label="‹"
        ariaLabel="previous page"
        disabled={prevDisabled}
        onClick={() => !prevDisabled && onPrev()}
      />
      <span
        data-testid="pager-current"
        style={{
          padding: "0 8px",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          fontWeight: 700,
          color: "var(--ink)",
          minWidth: 48,
          textAlign: "center",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {current} / {total}
      </span>
      <PagerButton
        testid="pager-next"
        label="›"
        ariaLabel="next page"
        disabled={nextDisabled}
        onClick={() => !nextDisabled && onNext()}
      />
    </div>
  );
}

function PagerButton({ testid, label, ariaLabel, disabled, onClick }) {
  return (
    <button
      type="button"
      data-testid={testid}
      data-disabled={disabled ? "true" : "false"}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={onClick}
      style={{
        height: 24,
        width: 24,
        background: "transparent",
        border: "none",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.3 : 1,
        fontFamily: "var(--font-mono)",
        fontSize: 16,
        fontWeight: 800,
        color: "var(--ink)",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {label}
    </button>
  );
}
