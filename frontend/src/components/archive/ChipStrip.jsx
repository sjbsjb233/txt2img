// Horizontal strip of applied filter chips. Scrolls horizontally when
// chips overflow; the `+ filter` trigger sits OUTSIDE the scroll
// container so its top-right notification badge is never clipped.

import { chipLabel, forEachChip, removeChip } from "./archiveFilter.js";

export default function ChipStrip({ applied, dynamic, onChange }) {
  const chips = [];
  forEachChip(applied, ({ propId, value }) => {
    chips.push({ propId, value });
  });

  return (
    <div
      data-testid="archive-chip-strip"
      style={{
        flex: "1 1 0",
        minWidth: 0,
        display: "flex",
        alignItems: "center",
        gap: 8,
        overflowX: "auto",
        overflowY: "hidden",
        whiteSpace: "nowrap",
        // Match the chip's 28px height + 1px breathing room top/bottom
        // so the row doesn't visually jump when the first chip lands.
        padding: "1px 0",
      }}
    >
      {chips.map(({ propId, value }) => {
        const v = typeof value === "boolean" ? "true" : String(value);
        return (
          <Chip
            key={`${propId}:${v}`}
            propId={propId}
            value={value}
            label={chipLabel(propId, value, dynamic)}
            onRemove={() => onChange(removeChip(applied, propId, value))}
          />
        );
      })}
    </div>
  );
}

export function FilterTrigger({ count, onClick }) {
  return (
    <button
      type="button"
      data-testid="archive-filter-trigger"
      onClick={onClick}
      style={{
        position: "relative",
        flexShrink: 0,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "6px 12px",
        borderRadius: 999,
        background: "transparent",
        color: "var(--ink-3)",
        border: "1px dashed var(--ink-3)",
        cursor: "pointer",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      + filter
      {count > 0 && (
        <span
          className="arch-chip-badge-pop"
          aria-label={`${count} filters applied`}
          style={{
            position: "absolute",
            top: -7,
            right: -7,
            minWidth: 18,
            height: 18,
            padding: "0 5px",
            borderRadius: 999,
            background: "var(--banana)",
            border: "1px solid var(--ink)",
            color: "var(--ink)",
            fontSize: 10,
            fontWeight: 700,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "var(--font-mono)",
          }}
        >
          {count}
        </span>
      )}
    </button>
  );
}

function Chip({ propId, value, label, onRemove }) {
  const v = typeof value === "boolean" ? "true" : String(value);
  return (
    <div
      data-testid={`chip-${propId}-${v}`}
      className="arch-chip-in"
      style={{
        flexShrink: 0,
        display: "inline-flex",
        alignItems: "stretch",
        borderRadius: 999,
        background: "var(--banana)",
        border: "1px solid var(--ink)",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        fontWeight: 600,
        color: "var(--ink)",
        whiteSpace: "nowrap",
        height: 28,
        overflow: "hidden",
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", padding: "0 10px" }}>
        {label}
      </span>
      <button
        type="button"
        data-testid={`chip-remove-${propId}-${v}`}
        aria-label={`remove filter ${propId} ${v}`}
        onClick={onRemove}
        style={{
          width: 22,
          background: "transparent",
          border: "none",
          borderLeft: "1px solid var(--ink)",
          cursor: "pointer",
          fontSize: 14,
          fontWeight: 700,
          color: "var(--ink)",
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
}
