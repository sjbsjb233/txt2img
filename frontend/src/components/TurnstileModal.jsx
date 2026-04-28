import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { loadTurnstile } from "../api/turnstile.js";

// Cloudflare Turnstile gate.
//
// Unlike the previous mockup, this version actually renders the Turnstile
// widget against the site key the backend hands down via /api/auth/captcha-check.
// The widget produces a token through its callback; we capture it in local
// state and forward it to `onContinue(token)` when the user confirms.
//
// `siteKey` may be missing in dev environments where the operator has not
// configured Turnstile at all — in that case we surface a friendly empty
// state and disable the Continue button. Production should always pass one.
export default function TurnstileModal({ open, onClose, onContinue, siteKey }) {
  const containerRef = useRef(null);
  const widgetIdRef = useRef(null);
  const [token, setToken] = useState("");
  const [loadError, setLoadError] = useState("");

  // Mount the widget when the modal opens; tear it down when it closes so
  // the next open gets a fresh challenge.
  useEffect(() => {
    if (!open) return;
    if (!siteKey) {
      setLoadError("Turnstile is not configured on the server.");
      return undefined;
    }
    let cancelled = false;
    setToken("");
    setLoadError("");

    loadTurnstile()
      .then((turnstile) => {
        if (cancelled || !containerRef.current) return;
        try {
          widgetIdRef.current = turnstile.render(containerRef.current, {
            sitekey: siteKey,
            theme: "light",
            callback: (tok) => setToken(tok),
            "expired-callback": () => setToken(""),
            "error-callback": () => setToken(""),
            "timeout-callback": () => setToken(""),
          });
        } catch (e) {
          setLoadError(e?.message || "Failed to render Turnstile widget.");
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setLoadError(e?.message || "Failed to load Turnstile.");
      });

    return () => {
      cancelled = true;
      const id = widgetIdRef.current;
      widgetIdRef.current = null;
      if (id != null && window.turnstile) {
        try { window.turnstile.remove(id); } catch { /* ignore */ }
      }
    };
  }, [open, siteKey]);

  // Keyboard shortcuts: Esc closes, Enter submits when a token is ready.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
      if (e.key === "Enter" && token) onContinue?.(token);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, onContinue, token]);

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
              padding: "16px",
              background: "#fffdf7",
              border: "1px solid var(--ink)",
              minHeight: 80,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {loadError ? (
              <div
                className="mono"
                style={{ fontSize: 12, color: "#c0392b", textAlign: "center" }}
              >
                {loadError}
              </div>
            ) : (
              <div ref={containerRef} />
            )}
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
            onClick={() => token && onContinue?.(token)}
            disabled={!token}
            className="btn ink shadowed"
            style={{
              height: 44,
              padding: "0 24px",
              color: "var(--banana)",
              opacity: token ? 1 : 0.5,
              cursor: token ? "pointer" : "not-allowed",
            }}
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
