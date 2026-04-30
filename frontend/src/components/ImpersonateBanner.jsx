import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getToken } from "../api/client.js";
import { decodeJwtPayload, expiryMs } from "../utils/jwt.js";
import { logout as logoutFlow, useAuth } from "../store/auth.js";

// Top banner shown on every page when the active token is an impersonate
// token (carries the `impersonator` claim). The banner reminds the
// admin they are acting as someone else and gives them a one-click exit.
//
// The banner reads the token directly rather than going through the
// auth store because we need to surface the impersonation regardless of
// whether the user object happens to be cached in localStorage.
export default function ImpersonateBanner() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [now, setNow] = useState(() => Date.now());

  // Re-render every 30s so the "remaining" countdown stays current.
  // We don't tick more often because the banner is read-only and we'd
  // rather not jitter the layout.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const token = getToken();
  const payload = token ? decodeJwtPayload(token) : null;
  if (!payload || typeof payload.impersonator !== "string" || !payload.impersonator) {
    return null;
  }

  const exp = expiryMs(token);
  const remaining = exp ? Math.max(0, exp - now) : 0;
  const minutes = Math.ceil(remaining / 60_000);

  const exit = async () => {
    await logoutFlow();
    navigate("/login", { replace: true });
  };

  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "8px 16px",
        background: "var(--banana)",
        color: "var(--ink)",
        borderBottom: "2px solid var(--ink)",
        fontFamily: "var(--font-sans)",
        fontSize: 12,
        fontWeight: 700,
        zIndex: 50,
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          background: "var(--ink)",
          borderRadius: "50%",
          flexShrink: 0,
        }}
      />
      <span
        className="mono caps"
        style={{ letterSpacing: "0.16em", fontWeight: 800 }}
      >
        IMPERSONATING
      </span>
      <span style={{ fontWeight: 700 }}>
        @{user?.username || payload.u || "(unknown)"}
      </span>
      <span
        className="mono"
        style={{ fontSize: 11, color: "var(--ink-3)", fontWeight: 600 }}
      >
        admin · {payload.impersonator}
        {exp ? ` · ${minutes}m left` : ""}
      </span>
      <span style={{ flex: 1 }} />
      <button
        type="button"
        onClick={exit}
        className="btn sm"
        style={{
          background: "var(--ink)",
          color: "var(--banana)",
          border: "1px solid var(--ink)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          fontWeight: 700,
          padding: "4px 10px",
          cursor: "pointer",
        }}
      >
        Exit impersonation
      </button>
    </div>
  );
}
