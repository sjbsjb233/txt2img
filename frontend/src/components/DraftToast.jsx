import { useEffect, useState } from "react";

// Honour ``prefers-reduced-motion`` — design doc §5.6 cuts the fade
// timings down to 80ms so the state still flips but no animation feel.
function useReducedMotion() {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handler = (e) => setReduced(e.matches);
    if (mq.addEventListener) mq.addEventListener("change", handler);
    else mq.addListener(handler);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", handler);
      else mq.removeListener(handler);
    };
  }, []);
  return reduced;
}

// One-time injected keyframes. Lives at module scope so re-renders
// don't re-add the style tag. We keep this in JS instead of the global
// stylesheet to keep the autosave feature self-contained.
let injected = false;
function ensureKeyframes() {
  if (injected || typeof document === "undefined") return;
  injected = true;
  const style = document.createElement("style");
  style.dataset.draftToast = "1";
  style.textContent = `
@keyframes draftToastIn {
  from { opacity: 0; transform: translateY(-2px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes draftToastOut {
  from { opacity: 1; }
  to { opacity: 0; }
}`;
  document.head.appendChild(style);
}

/**
 * Draft autosave indicator — sits in the CreatePage TopBar to the left
 * of the Clear button. Renders nothing when there's no toast to show.
 *
 * Animation uses CSS @keyframes so the fade-in fires reliably the moment
 * the element mounts (a transition relies on a previous computed style
 * to interpolate from, which React's batched first render skips).
 */
export default function DraftToast({ value }) {
  ensureKeyframes();
  const reduced = useReducedMotion();
  if (!value) return null;
  const fadeIn = reduced ? 80 : 240;
  const fadeOut = reduced ? 80 : 360;
  const animation = value.visible
    ? `draftToastIn ${fadeIn}ms ease-out forwards`
    : `draftToastOut ${fadeOut}ms ease-in forwards`;
  return (
    <div
      data-testid="draft-toast"
      data-kind={value.kind}
      data-visible={value.visible ? "true" : "false"}
      key={value.key}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 7,
        marginRight: 12,
        pointerEvents: "none",
        animation,
        // Final state when animation finishes (forwards), but also a
        // safe initial paint before the animation runs.
        opacity: value.visible ? 1 : 0,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: "var(--banana)",
          flexShrink: 0,
        }}
      />
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "var(--ink-3)",
          whiteSpace: "nowrap",
        }}
      >
        {value.text}
      </span>
    </div>
  );
}
