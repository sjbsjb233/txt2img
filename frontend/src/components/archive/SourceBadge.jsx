// SourceBadge — small "▶#147" / "▶#147-3" pill in the top-right of an
// archive thumbnail, showing where this row was derived from.
// See PRD §5.5.
//
// Glyph: ``▶`` (right-pointing triangle) reads as "branched / forked
// from" rather than ``↑`` (up arrow) — which kept getting confused with
// "go back up". The kind chip (filled / hollow square) carries the
// derivation kind (mask edit / outpaint).

import { useRef, useState } from "react";

import HoverPortal from "../HoverPortal.jsx";

export default function SourceBadge({
  parentSeqNo,
  parentOrder,
  derivationKind,
  onClick,
  title,
  unknownOrder,
}) {
  const [hover, setHover] = useState(false);
  const wrapRef = useRef(null);
  if (parentSeqNo == null) return null;
  const labelTag = parentOrder
    ? `#${parentSeqNo}-${parentOrder}`
    : `#${parentSeqNo}`;
  const kindWord =
    derivationKind === "mask_edit"
      ? "mask edit"
      : derivationKind === "outpaint"
        ? "outpaint"
        : "derivation";
  const tooltipText =
    title ||
    (unknownOrder
      ? `derived from #${parentSeqNo} (source order unknown)`
      : `${kindWord} of ${labelTag} · click to open parent`);
  return (
    <div
      className="arch-source-badge-wrap"
      ref={wrapRef}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <button
        type="button"
        className={`arch-source-badge arch-source-badge--${derivationKind || "default"}`}
        onClick={(e) => {
          e.stopPropagation();
          onClick?.();
        }}
        title={tooltipText}
        data-testid={`arch-source-badge-${parentSeqNo}${parentOrder ? `-${parentOrder}` : ""}`}
        data-derivation-kind={derivationKind || ""}
        aria-label={tooltipText}
      >
        <span
          className={`arch-source-badge__kind arch-source-badge__kind--${derivationKind || "default"}`}
          aria-hidden="true"
        />
        <span className="arch-source-badge__glyph" aria-hidden="true">▶</span>
        <span className="arch-source-badge__label">{labelTag}</span>
      </button>
      <HoverPortal
        anchorRef={wrapRef}
        open={hover}
        preferredSide="below"
        align="end"
      >
        <div className="arch-source-badge-hover" role="tooltip">
          <div className="arch-source-badge-hover__row">
            <span className="arch-source-badge-hover__key">source</span>
            <span className="arch-source-badge-hover__val">{labelTag}</span>
          </div>
          <div className="arch-source-badge-hover__row">
            <span className="arch-source-badge-hover__key">kind</span>
            <span className="arch-source-badge-hover__val">{kindWord}</span>
          </div>
          {unknownOrder && (
            <div className="arch-source-badge-hover__row">
              <span className="arch-source-badge-hover__key">note</span>
              <span className="arch-source-badge-hover__val">order unknown (legacy row)</span>
            </div>
          )}
          <div className="arch-source-badge-hover__hint">click → open parent</div>
        </div>
      </HoverPortal>
    </div>
  );
}
