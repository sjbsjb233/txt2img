// Full-screen overlay shown when the SSE client has tried 5+ times in a
// row to reconnect and failed. The button calls ``reconnectNow`` on the
// SSE store; once a connection comes back the App layer hides the
// overlay automatically because ``connection_state.status`` flips to
// ``'open'``.
//
// Visual brief from design doc §12.3:
//   - Centered card with 1px ink border and a hard 5px ink shadow,
//     consistent with the project's archive popovers and stat cards.
//   - English copy. We deliberately do not localize at this layer; the
//     errorCopy table is the single i18n surface.
//   - Mono attempts counter under the title, banana CTA button.
//
// The overlay is rendered above all sidebars / content via a fixed
// position + a tinted backdrop. We intentionally do *not* block
// interaction with the background completely — users may want to keep
// reading the last archive snapshot while the connection is down — so
// the backdrop is semi-transparent and ``pointer-events: none`` on
// everything except the card itself.

import { reconnectNow } from "../store/sse.js";

export default function ConnectionLost({ attempts = 5 }) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(25, 23, 20, 0.18)",
        backdropFilter: "blur(2px)",
        WebkitBackdropFilter: "blur(2px)",
        zIndex: 9999,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          background: "#fffdf7",
          border: "1px solid var(--ink)",
          padding: "28px 32px 24px",
          minWidth: 320,
          maxWidth: 420,
          textAlign: "center",
          boxShadow: "5px 5px 0 var(--ink)",
          pointerEvents: "auto",
        }}
      >
        <div
          aria-hidden="true"
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 36,
            height: 36,
            border: "1px solid var(--ink)",
            background: "var(--banana)",
            margin: "0 auto 14px",
            fontFamily: "var(--font-mono)",
            fontSize: 16,
            fontWeight: 700,
            // Pulse subtly so the user knows it's live, not a frozen
            // screenshot. ``prefers-reduced-motion`` disables the
            // animation via the global rule on .route-stage; we add
            // a local fallback for the dot just to be safe.
            animation: "connWarnPulse 1.6s ease-in-out infinite",
          }}
        >
          !
        </div>
        <div
          className="display"
          style={{
            fontSize: 24,
            fontWeight: 900,
            letterSpacing: "-0.02em",
            color: "var(--ink)",
            lineHeight: 1.1,
          }}
        >
          Connection lost
        </div>
        <div
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--ink-3)",
            marginTop: 6,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
        >
          trying to reconnect · attempt {attempts}
        </div>
        <button
          type="button"
          className="btn sm shadowed"
          style={{ marginTop: 16 }}
          onClick={() => reconnectNow()}
        >
          retry now
        </button>
      </div>
      <style>{`
        @keyframes connWarnPulse {
          0%, 100% { transform: translateZ(0) scale(1); }
          50%      { transform: translateZ(0) scale(1.08); }
        }
        @media (prefers-reduced-motion: reduce) {
          [data-conn-lost-pulse] { animation: none !important; }
        }
      `}</style>
    </div>
  );
}
