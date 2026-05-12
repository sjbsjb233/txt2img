// MixedSourceBadge — stacked-card pill shown on a batch-set whose
// members have more than one distinct (non-null) parent. Clicking opens
// a popover listing "member ← parent" pairs so the user can pick which
// ancestor to navigate to.
//
// Glyph: ``▶`` (right-pointing branch arrow) instead of ``↑`` — same
// rationale as the single-source badge.

import { useEffect, useRef, useState } from "react";

export default function MixedSourceBadge({ distinctCount, members, onPick }) {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onDocClick(e) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target)) setOpen(false);
    }
    if (open) {
      document.addEventListener("mousedown", onDocClick);
      return () => document.removeEventListener("mousedown", onDocClick);
    }
    return undefined;
  }, [open]);

  if (!members || members.length === 0) return null;

  const memberSummary = members
    .slice(0, 3)
    .map((m) => {
      const parent = m.parentSeqNo == null
        ? "—"
        : `#${m.parentSeqNo}${m.parentOrder ? `-${m.parentOrder}` : ""}`;
      return `#${m.memberSeqNo} ← ${parent}`;
    })
    .join(" · ");
  const tooltipText = `${distinctCount} distinct sources · click to expand`;

  return (
    <div
      className="arch-source-badge-mixed-wrap"
      ref={ref}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <button
        type="button"
        className="arch-source-badge arch-source-badge--mixed"
        title={tooltipText}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        data-testid="arch-source-badge-mixed"
        aria-label={tooltipText}
      >
        <span className="arch-source-badge__stack-bg-1" aria-hidden="true" />
        <span className="arch-source-badge__stack-bg-2" aria-hidden="true" />
        <span className="arch-source-badge__glyph" aria-hidden="true">▶</span>
        <span className="arch-source-badge__label">mixed · {distinctCount}</span>
      </button>
      {hover && !open && (
        <div className="arch-source-badge-hover" role="tooltip">
          <div className="arch-source-badge-hover__row">
            <span className="arch-source-badge-hover__key">sources</span>
            <span className="arch-source-badge-hover__val">{distinctCount} distinct</span>
          </div>
          <div className="arch-source-badge-hover__row">
            <span className="arch-source-badge-hover__key">members</span>
            <span className="arch-source-badge-hover__val">{members.length}</span>
          </div>
          <div className="arch-source-badge-hover__preview">{memberSummary}</div>
          <div className="arch-source-badge-hover__hint">click → expand list</div>
        </div>
      )}
      {open && (
        <div
          className="arch-source-badge-popover"
          data-testid="arch-source-badge-popover"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="arch-source-badge-popover__head">members → sources</div>
          <div className="arch-source-badge-popover__list">
            {members.map((m, i) => (
              <button
                type="button"
                key={i}
                className="arch-source-badge-popover__row"
                disabled={m.parentSeqNo == null}
                onClick={() => {
                  setOpen(false);
                  onPick?.(m);
                }}
              >
                <span className="arch-source-badge-popover__from">#{m.memberSeqNo}</span>
                <span className="arch-source-badge-popover__arrow">←</span>
                <span className="arch-source-badge-popover__to">
                  {m.parentSeqNo == null
                    ? "—"
                    : `#${m.parentSeqNo}${m.parentOrder ? `-${m.parentOrder}` : ""}`}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
