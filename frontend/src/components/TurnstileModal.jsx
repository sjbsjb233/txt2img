import { useEffect } from "react";
import Icon from "./Icon.jsx";

export default function TurnstileModal({ open, onClose, onContinue }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
      if (e.key === "Enter") onContinue?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, onContinue]);

  if (!open) return null;
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 80,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#19171488",
        backdropFilter: "blur(2px)",
        animation: "tFade 160ms ease-out",
      }}
    >
      <style>{`
        @keyframes tFade { from { opacity: 0; } to { opacity: 1; } }
        @keyframes tPop { from { opacity: 0; transform: translateY(8px) scale(.98); } to { opacity: 1; transform: none; } }
        @keyframes tDashSpin { to { stroke-dashoffset: -32; } }
      `}</style>
      <div
        style={{
          width: 560,
          background: "var(--paper)",
          border: "1px solid var(--ink)",
          boxShadow: "8px 8px 0 var(--ink)",
          animation: "tPop 220ms cubic-bezier(.2,.85,.2,1)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            padding: "14px 20px",
            background: "var(--ink)",
            color: "var(--paper)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span className="mono caps" style={{ fontSize: 11, letterSpacing: "0.18em" }}>
            SECURITY · TURNSTILE
          </span>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--paper)",
              cursor: "pointer",
              padding: 4,
            }}
          >
            <Icon name="close" size={14} />
          </button>
        </div>

        <div style={{ padding: "32px 36px 28px" }}>
          <div
            className="mono caps"
            style={{ fontSize: 11, color: "var(--ink-3)", letterSpacing: "0.18em" }}
          >
            STEP 02 OF 02 — JUST CHECKING
          </div>
          <h2
            className="display"
            style={{
              fontSize: 44,
              fontWeight: 900,
              letterSpacing: "-0.035em",
              lineHeight: 1,
              margin: "16px 0 0",
            }}
          >
            Quick check —
          </h2>
          <h2
            className="display"
            style={{
              fontSize: 44,
              fontWeight: 900,
              fontStyle: "italic",
              letterSpacing: "-0.035em",
              lineHeight: 1,
              margin: 0,
              color: "var(--banana-deep)",
            }}
          >
            are you human?
          </h2>

          <p
            style={{
              marginTop: 22,
              fontSize: 14,
              lineHeight: 1.55,
              color: "var(--ink-2)",
              maxWidth: 460,
            }}
          >
            A second of verification keeps the studio clean.
            <br />
            No puzzles, no clicking buses. Just a quiet handshake.
          </p>

          <div
            style={{
              marginTop: 22,
              padding: "14px 16px",
              background: "#fffdf7",
              border: "1px solid var(--ink)",
              display: "flex",
              alignItems: "center",
              gap: 14,
            }}
          >
            <svg width="40" height="40" viewBox="0 0 40 40" style={{ flexShrink: 0 }}>
              <rect
                x="3"
                y="3"
                width="34"
                height="34"
                fill="none"
                stroke="var(--banana-deep)"
                strokeWidth="2"
                strokeDasharray="4 3"
                style={{ animation: "tDashSpin 1.6s linear infinite" }}
              />
            </svg>
            <div style={{ flex: 1 }}>
              <div
                className="display"
                style={{ fontStyle: "italic", fontSize: 17, fontWeight: 700 }}
              >
                Verifying you're human…
              </div>
              <div
                className="mono"
                style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 3 }}
              >
                Cloudflare Turnstile · usually under a second
              </div>
            </div>
            <div
              style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}
            >
              <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                CF
              </span>
              <span
                style={{
                  width: 18,
                  height: 18,
                  background: "var(--banana)",
                  border: "1px solid var(--ink)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <span style={{ fontSize: 9, color: "var(--ink)" }}>▶</span>
              </span>
            </div>
          </div>

          <div
            style={{
              marginTop: 24,
              paddingTop: 18,
              borderTop: "1px solid var(--rule-2)",
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}
            >
              WHY THIS APPEARS
            </div>
            <div
              style={{
                marginTop: 8,
                fontFamily: "var(--font-display)",
                fontStyle: "italic",
                fontSize: 14,
                color: "var(--ink-2)",
                lineHeight: 1.5,
                maxWidth: 420,
              }}
            >
              You've crossed today's free quota, or your network looks unusual. We run
              this once, then leave you alone.
            </div>
          </div>
        </div>

        <div
          style={{
            padding: "16px 24px 22px",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <button
            onClick={onClose}
            className="btn"
            style={{ height: 44, padding: "0 24px" }}
          >
            Cancel
          </button>
          <button
            onClick={onContinue}
            className="btn ink shadowed"
            style={{ height: 44, padding: "0 24px", color: "var(--banana)" }}
          >
            Continue →{" "}
            <span
              className="kbd"
              style={{ marginLeft: 6, background: "var(--banana)", color: "var(--ink)" }}
            >
              ↵
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
