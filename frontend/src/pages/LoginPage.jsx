import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import TurnstileModal from "../components/TurnstileModal.jsx";
import { apiFetch, getApiBase, setApiBase, setToken } from "../api/client.js";

const CAPTIONS = [
  "issue №004 — apr 2026",
  "developing image 0428…",
  "studio session active",
  "rendering at 35mm · 1/250s",
];

export default function LoginPage() {
  const [showTurnstile, setShowTurnstile] = useState(false);
  const [tick, setTick] = useState(0);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [apiBase, setApiBaseState] = useState(getApiBase());
  const [healthMsg, setHealthMsg] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 100);
    return () => clearInterval(id);
  }, []);

  const cap = CAPTIONS[Math.floor(tick / 60) % CAPTIONS.length];
  const visible = cap.slice(
    0,
    tick % 60 > 50 ? cap.length : Math.min(cap.length, Math.floor((tick % 60) / 2))
  );

  const doLogin = async (captchaToken) => {
    const data = await apiFetch("/api/auth/login", {
      method: "POST",
      body: { username, password, captcha_token: captchaToken },
      auth: false,
    });
    setToken(data.access_token);
    localStorage.setItem("user", JSON.stringify(data.user));
    navigate("/");
  };

  const submit = async (e) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    if (!username || !password) {
      setError("Username and password are required.");
      return;
    }
    setLoading(true);
    try {
      const { required } = await apiFetch("/api/auth/captcha-check", {
        method: "POST",
        body: { username },
        auth: false,
      });
      if (required) {
        setShowTurnstile(true);
      } else {
        await doLogin(null);
      }
    } catch (err) {
      setError(err.message || "Login failed.");
    } finally {
      setLoading(false);
    }
  };

  const completeLogin = async () => {
    setShowTurnstile(false);
    setError("");
    setLoading(true);
    try {
      await doLogin("dev-stub-token");
    } catch (err) {
      setError(err.message || "Login failed.");
    } finally {
      setLoading(false);
    }
  };

  const onApiBaseChange = (value) => {
    setApiBaseState(value);
    setApiBase(value);
    setHealthMsg("");
  };

  const testApiBase = async () => {
    setHealthMsg("…");
    try {
      const data = await apiFetch("/api/health", { auth: false });
      setHealthMsg(data && data.ok ? "ok" : "unexpected response");
    } catch (err) {
      setHealthMsg(`error: ${err.message}`);
    }
  };

  return (
    <div
      style={{
        width: "100vw",
        minHeight: "100vh",
        background: "var(--paper)",
        display: "grid",
        gridTemplateColumns: "1.1fr 0.9fr",
        position: "relative",
        overflow: "hidden",
        color: "var(--ink)",
      }}
    >
      <style>{`
        @keyframes loginFloat { 0%,100% { transform: translateY(0) rotate(-2deg); } 50% { transform: translateY(-8px) rotate(2deg); } }
        @keyframes loginPulse { 0%,100% { opacity: 0.08; } 50% { opacity: 0.14; } }
        @keyframes loginScan { 0% { transform: translateY(-100%); } 100% { transform: translateY(100%); } }
        @keyframes loginShimmer { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
        @keyframes loginRotate { from { transform: rotate(0); } to { transform: rotate(360deg); } }
        @keyframes loginBlink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
        @keyframes loginGlyph { 0%,100% { transform: translateY(0); opacity: .12; } 50% { transform: translateY(-12px); opacity: .22; } }
      `}</style>

      <div
        style={{
          background: "var(--ink)",
          color: "var(--paper)",
          padding: "56px 64px",
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, position: "relative", zIndex: 2 }}>
          <div
            style={{
              width: 44,
              height: 44,
              background: "var(--banana)",
              border: "1px solid var(--banana-deep)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              position: "relative",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 17,
                fontWeight: 800,
                color: "var(--ink)",
                letterSpacing: "-0.04em",
              }}
            >
              t2i
            </span>
          </div>
          <div>
            <div className="display" style={{ fontSize: 16, fontWeight: 800 }}>
              txt<span style={{ fontStyle: "italic", color: "var(--banana)" }}>2</span>img
            </div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--banana)" }}>
              /studio — console
            </div>
          </div>
        </div>

        <div style={{ marginTop: 110, position: "relative", zIndex: 2 }}>
          <div className="mono caps" style={{ fontSize: 11, color: "var(--banana)", height: 14 }}>
            {visible}
            <span style={{ animation: "loginBlink 1s steps(2) infinite" }}>▌</span>
          </div>

          <h1
            className="display"
            style={{
              fontSize: 92,
              fontWeight: 900,
              letterSpacing: "-0.05em",
              lineHeight: 0.9,
              margin: "16px 0",
              color: "var(--paper)",
            }}
          >
            Make
            <br />
            <span
              style={{
                fontStyle: "italic",
                color: "var(--banana)",
                display: "inline-block",
                animation: "loginShimmer 4s ease-in-out infinite",
                backgroundImage:
                  "linear-gradient(90deg,var(--banana) 0%, #fff5c5 50%, var(--banana) 100%)",
                backgroundSize: "200% 100%",
                WebkitBackgroundClip: "text",
                backgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              images
            </span>
            ,<br />
            not slop.
          </h1>
          <div
            style={{
              fontSize: 16,
              color: "var(--paper-2)",
              maxWidth: 440,
              lineHeight: 1.5,
              marginTop: 24,
            }}
          >
            A studio-grade console around one idea: generate, triage, ship. No endless tabs.
            No lost pictures. Just the good ones, pinned.
          </div>
        </div>

        <div
          style={{
            position: "absolute",
            bottom: 56,
            left: 64,
            right: 64,
            display: "flex",
            gap: 40,
            paddingTop: 20,
            borderTop: "1px solid #ffffff22",
            zIndex: 2,
          }}
        >
          {[
            ["1.4M", "images shipped"],
            ["42ms", "median pick-time"],
            ["98.7%", "uptime 30d"],
          ].map(([v, l]) => (
            <div key={l}>
              <div
                className="ticker"
                style={{
                  fontSize: 28,
                  fontWeight: 900,
                  color: "var(--banana)",
                  letterSpacing: "-0.03em",
                }}
              >
                {v}
              </div>
              <div
                className="mono caps"
                style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 4 }}
              >
                {l}
              </div>
            </div>
          ))}
        </div>

        <div
          style={{
            position: "absolute",
            right: -50,
            top: 60,
            fontSize: 460,
            fontFamily: "var(--font-display)",
            fontWeight: 900,
            color: "var(--banana)",
            fontStyle: "italic",
            letterSpacing: "-0.1em",
            lineHeight: 1,
            zIndex: 1,
            animation: "loginPulse 6s ease-in-out infinite",
          }}
        >
          2
        </div>

        {[
          { c: "#9bdac5", t: 0, l: 78, d: 0 },
          { c: "#e8a98a", t: 22, l: 86, d: 1.2 },
          { c: "#f5c862", t: 50, l: 72, d: 0.4 },
          { c: "#7fb6e3", t: 70, l: 90, d: 2.0 },
        ].map((p, i) => (
          <div
            key={i}
            style={{
              position: "absolute",
              top: `${p.t}%`,
              left: `${p.l}%`,
              width: 60,
              height: 60,
              background: p.c,
              border: "1px solid #00000033",
              opacity: 0.7,
              animation: `loginFloat 4.5s ease-in-out ${p.d}s infinite`,
              zIndex: 1,
            }}
          />
        ))}

        <div
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            zIndex: 1,
            backgroundImage:
              "linear-gradient(180deg, transparent 0%, transparent 49%, var(--banana) 50%, transparent 51%, transparent 100%)",
            opacity: 0.05,
            mixBlendMode: "overlay",
            backgroundSize: "100% 200%",
            animation: "loginScan 8s linear infinite",
          }}
        />

        <div
          style={{
            position: "absolute",
            left: 64,
            bottom: 180,
            width: 60,
            height: 60,
            opacity: 0.6,
            animation: "loginRotate 18s linear infinite",
            zIndex: 1,
          }}
        >
          <svg viewBox="0 0 60 60" width="60" height="60" fill="none" stroke="var(--banana)" strokeWidth="1">
            <circle cx="30" cy="30" r="28" />
            {Array.from({ length: 24 }).map((_, i) => (
              <line
                key={i}
                x1="30"
                y1="2"
                x2="30"
                y2={i % 6 === 0 ? "8" : "5"}
                transform={`rotate(${i * 15} 30 30)`}
              />
            ))}
          </svg>
        </div>

        {["px", "1K", "↵", "2×"].map((g, i) => (
          <div
            key={g}
            style={{
              position: "absolute",
              top: `${20 + i * 18}%`,
              left: `${4 + (i % 2) * 4}%`,
              fontFamily: "var(--font-mono)",
              fontSize: 14,
              color: "var(--banana)",
              opacity: 0.18,
              zIndex: 1,
              animation: `loginGlyph 5s ease-in-out ${i * 0.7}s infinite`,
            }}
          >
            {g}
          </div>
        ))}
      </div>

      <form
        onSubmit={submit}
        style={{
          padding: "56px 72px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          background: "var(--paper)",
        }}
      >
        <div style={{ maxWidth: 420 }}>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            Password login
          </div>
          <h2
            className="display"
            style={{
              fontSize: 44,
              fontWeight: 900,
              letterSpacing: "-0.035em",
              margin: "8px 0 8px",
            }}
          >
            Enter the studio.
          </h2>
          <div style={{ fontSize: 13, color: "var(--ink-3)", marginBottom: 32 }}>
            Cloudflare Turnstile is required for every session.
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <label className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                Username
              </label>
              <input
                className="inp"
                placeholder="liuxi"
                style={{ marginTop: 4, height: 44 }}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                disabled={loading}
              />
            </div>
            <div>
              <label className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                Password
              </label>
              <input
                className="inp"
                type="password"
                placeholder="••••••••••"
                style={{ marginTop: 4, height: 44 }}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                disabled={loading}
              />
            </div>

            <button
              type="submit"
              className="btn primary shadowed lg"
              style={{ width: "100%", marginTop: 10, fontSize: 15, opacity: loading ? 0.6 : 1 }}
              disabled={loading}
            >
              {loading ? "Signing in…" : "Sign in →"}
            </button>

            {error ? (
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  color: "#c0392b",
                  background: "#fdecea",
                  border: "1px solid #c0392b",
                  padding: "8px 10px",
                  marginTop: 4,
                }}
              >
                {error}
              </div>
            ) : null}

            <details style={{ marginTop: 8 }}>
              <summary
                className="mono"
                style={{
                  fontSize: 11,
                  color: "var(--ink-3)",
                  cursor: "pointer",
                  outline: "none",
                }}
              >
                ◢ Backend: {apiBase}
              </summary>
              <div
                style={{
                  marginTop: 10,
                  padding: 12,
                  background: "var(--paper-2)",
                  border: "1px solid var(--ink)",
                  display: "flex",
                  gap: 6,
                  alignItems: "center",
                }}
              >
                <input
                  className="inp"
                  value={apiBase}
                  onChange={(e) => onApiBaseChange(e.target.value)}
                  style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}
                />
                <button type="button" className="btn sm" onClick={testApiBase}>
                  Test
                </button>
              </div>
              {healthMsg ? (
                <div
                  className="mono"
                  style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}
                >
                  {healthMsg}
                </div>
              ) : null}
            </details>
          </div>

          <div
            style={{
              marginTop: 40,
              paddingTop: 20,
              borderTop: "1px solid var(--rule-2)",
              display: "flex",
              justifyContent: "space-between",
              fontSize: 11,
              color: "var(--ink-3)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span>v2.4.0 · build 8f3a</span>
            <span>© txt2img studio</span>
          </div>
        </div>
      </form>

      <TurnstileModal
        open={showTurnstile}
        onClose={() => setShowTurnstile(false)}
        onContinue={completeLogin}
      />
    </div>
  );
}
