// HoverPortal — renders a tooltip-style card in a body-level portal,
// anchored to ``anchorRef`` and positioned with ``position: fixed`` so it
// escapes every ``overflow: hidden`` ancestor (and stacking-context
// surprises) that would otherwise clip the popup.
//
// Why this exists:
//   - LineageNode hover lived inside a scroller with ``overflow-x: hidden``
//     so the popup was clipped when the node was in a right-side lane.
//   - SourceBadge hover lived inside ``arch-thumb { overflow: hidden }`` so
//     the popup was clipped by the thumbnail's bottom edge.
//
// Behaviour:
//   - Computes placement against the anchor's viewport rect on every open
//     (and on resize / ancestor scroll).
//   - Auto-flips the preferred side when there isn't enough room — e.g.
//     ``right`` flips to ``left`` if the anchor sits too close to the
//     right edge; ``below`` flips to ``above`` if there's no room below.
//   - Clamps the orthogonal axis to keep the card inside the viewport.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const VIEWPORT_PADDING = 4;

export default function HoverPortal({
  anchorRef,
  open,
  preferredSide = "right",
  align = "start",
  offset = 12,
  zIndex = 5000,
  pointerEvents = "none",
  children,
}) {
  const cardRef = useRef(null);
  const [pos, setPos] = useState(null);

  // useLayoutEffect so the first paint already has correct coordinates —
  // otherwise the card would flash at (0,0) for one frame.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return undefined;
    }
    function compute() {
      const anchor = anchorRef?.current;
      const card = cardRef.current;
      if (!anchor || !card) return;
      const a = anchor.getBoundingClientRect();
      const c = card.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const cw = c.width;
      const ch = c.height;

      // Pick a side, flipping when the preferred direction overflows.
      let side = preferredSide;
      if (side === "right" && a.right + offset + cw > vw - VIEWPORT_PADDING)
        side = "left";
      else if (side === "left" && a.left - offset - cw < VIEWPORT_PADDING)
        side = "right";
      else if (side === "below" && a.bottom + offset + ch > vh - VIEWPORT_PADDING)
        side = "above";
      else if (side === "above" && a.top - offset - ch < VIEWPORT_PADDING)
        side = "below";

      let left;
      let top;
      if (side === "right" || side === "left") {
        left = side === "right" ? a.right + offset : a.left - offset - cw;
        if (align === "start") top = a.top;
        else if (align === "end") top = a.bottom - ch;
        else top = a.top + a.height / 2 - ch / 2;
      } else {
        top = side === "below" ? a.bottom + offset : a.top - offset - ch;
        if (align === "start") left = a.left;
        else if (align === "end") left = a.right - cw;
        else left = a.left + a.width / 2 - cw / 2;
      }

      // Clamp inside the viewport (so a tall card next to a top-anchored
      // node doesn't run off the bottom).
      left = Math.max(
        VIEWPORT_PADDING,
        Math.min(vw - cw - VIEWPORT_PADDING, left)
      );
      top = Math.max(
        VIEWPORT_PADDING,
        Math.min(vh - ch - VIEWPORT_PADDING, top)
      );

      setPos((prev) =>
        prev && prev.left === left && prev.top === top ? prev : { left, top }
      );
    }
    // Two passes: first paint with the card hidden to measure, then
    // re-measure once children have rendered (e.g. an <img> loaded).
    compute();
    const raf = requestAnimationFrame(compute);
    window.addEventListener("resize", compute);
    window.addEventListener("scroll", compute, true);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", compute);
      window.removeEventListener("scroll", compute, true);
    };
  }, [open, anchorRef, preferredSide, align, offset]);

  if (!open) return null;
  return createPortal(
    <div
      ref={cardRef}
      style={{
        position: "fixed",
        left: pos?.left ?? -9999,
        top: pos?.top ?? -9999,
        visibility: pos ? "visible" : "hidden",
        zIndex,
        pointerEvents,
      }}
    >
      {children}
    </div>,
    document.body
  );
}
