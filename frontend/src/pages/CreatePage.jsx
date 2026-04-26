import { useState } from "react";
import Icon from "../components/Icon.jsx";
import TopBar from "../components/TopBar.jsx";
import TurnstileModal from "../components/TurnstileModal.jsx";

function ModelLogo({ kind, size = 26 }) {
  const box = {
    width: size,
    height: size,
    border: "1px solid var(--ink)",
    background: "#fffdf7",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    position: "relative",
    overflow: "hidden",
  };
  if (kind === "chatgpt") {
    return (
      <span style={{ ...box, background: "var(--ink)" }}>
        <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 16 16" fill="none">
          <path
            d="M8 1.5l5.5 3.2v6.6L8 14.5l-5.5-3.2V4.7L8 1.5z"
            stroke="var(--banana)"
            strokeWidth="1.3"
          />
          <path d="M5 6l3 1.7L11 6M8 7.7v4.5" stroke="var(--banana)" strokeWidth="1.3" />
        </svg>
      </span>
    );
  }
  if (kind === "flash") {
    return (
      <span style={{ ...box, background: "var(--banana)" }}>
        <svg width={size * 0.5} height={size * 0.6} viewBox="0 0 10 14" fill="none">
          <path d="M5.5 1L1 8h3l-1 5 5-7H5l1-5z" fill="var(--ink)" />
        </svg>
      </span>
    );
  }
  if (kind === "draft") {
    return (
      <span style={box}>
        <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 16 16" fill="none">
          <rect
            x="2.5"
            y="2.5"
            width="11"
            height="11"
            stroke="var(--ink)"
            strokeWidth="1.3"
            strokeDasharray="2 1.5"
          />
          <path d="M5 8h6M8 5v6" stroke="var(--ink)" strokeWidth="1.3" />
        </svg>
      </span>
    );
  }
  return null;
}

const MODELS = [
  {
    id: "chatgpt",
    name: "ChatGPT Images 2.0",
    tag: "RECOMMENDED",
    logo: "chatgpt",
    blurb:
      "OpenAI's flagship — best for editorial, photoreal, brand. Strong text & composition control.",
  },
  {
    id: "flash",
    name: "Flash Diffuser",
    tag: "FAST",
    logo: "flash",
    blurb: "Snappy generations. Good for sketching and exploring directions before committing.",
  },
  {
    id: "draft",
    name: "Draft Lite",
    tag: "DRAFT",
    logo: "draft",
    blurb: "Lightweight rough pass. Tiny preview thumbnails to validate composition first.",
  },
];

const REFS = [
  { c: "#E8D5B0", l: "banana_01.jpg" },
  { c: "#4A5D3F", l: "leaf.png" },
  { c: "#C97B5E", l: "clay.jpg" },
];

const ASPECTS = [
  { r: "1:1", w: 40, h: 40 },
  { r: "4:3", w: 48, h: 36 },
  { r: "3:4", w: 36, h: 48 },
  { r: "16:9", w: 56, h: 32 },
  { r: "9:16", w: 28, h: 52 },
  { r: "21:9", w: 60, h: 26 },
];

const SIZES = [
  { id: "512", label: "512", note: "preview" },
  { id: "1K", label: "1K", note: "balanced" },
  { id: "2K", label: "2K", note: "print" },
  { id: "4K", label: "4K", note: "max" },
];

const SESSIONS = [
  { n: "Editorial Cover Shoot", bound: true, c: 24 },
  { n: "Q2 Brand Refresh", bound: false, c: 56 },
  { n: "Client · Acme", bound: false, c: 12 },
];

export default function CreatePage() {
  const [model, setModel] = useState("chatgpt");
  const [aspect, setAspect] = useState("1:1");
  const [size, setSize] = useState("1K");
  const [count, setCount] = useState(4);
  const [temp, setTemp] = useState(0.7);
  const [thinking, setThinking] = useState("mid");
  const [showTurnstile, setShowTurnstile] = useState(false);
  const [prompt, setPrompt] = useState(
    "A still-life of three overripe bananas on a marble slab, flemish painting, warm window light, shallow depth of field, 35mm"
  );

  return (
    <div style={{ flex: 1, overflowY: "auto", background: "var(--paper)", position: "relative" }}>
      <TopBar
        crumb="Create / Single Job"
        title={
          <>
            Make a <span style={{ fontStyle: "italic" }}>thing</span>.
          </>
        }
        subtitle="Text prompt, up to 14 reference images, any provider. Drafts autosave every keystroke."
        right={
          <>
            <span className="chip">
              <Icon name="check" size={10} /> DRAFT SAVED · 2s ago
            </span>
            <button className="btn sm ghost">Clear</button>
          </>
        }
      />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", padding: 0 }}>
        <div style={{ padding: "28px 36px", borderRight: "1px solid var(--ink)" }}>
          <div
            style={{
              border: "1px solid var(--ink)",
              background: "#fffdf7",
              position: "relative",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "10px 16px",
                borderBottom: "1px solid var(--ink)",
                background: "var(--paper-2)",
              }}
            >
              <div className="mono caps" style={{ fontSize: 10 }}>
                § Prompt
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="chip">ENHANCE</button>
                <button className="chip">TEMPLATES</button>
                <button className="chip">{prompt.length} chars</button>
              </div>
            </div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={6}
              style={{
                width: "100%",
                border: "none",
                outline: "none",
                padding: "22px 24px",
                fontFamily: "var(--font-display)",
                fontSize: 22,
                lineHeight: 1.4,
                background: "transparent",
                color: "var(--ink)",
                letterSpacing: "-0.01em",
                resize: "vertical",
              }}
            />
            <div
              style={{
                padding: "8px 16px",
                borderTop: "1px dashed var(--rule-2)",
                background: "var(--paper)",
                display: "flex",
                justifyContent: "space-between",
                fontSize: 11,
                color: "var(--ink-3)",
              }}
            >
              <span className="mono">
                Tip: mention <b>subject · style · light · composition · lens</b>
              </span>
              <span className="mono">⌘↵ to generate</span>
            </div>
          </div>

          <div style={{ marginTop: 24 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: 12,
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span
                  className="display"
                  style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em" }}
                >
                  Reference images
                </span>
                <span className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                  3 / 14
                </span>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="chip">
                  <Icon name="upload" size={10} /> UPLOAD
                </button>
                <button className="chip">PASTE ⌘V</button>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 8 }}>
              {REFS.map((r, i) => (
                <div
                  key={i}
                  style={{
                    aspectRatio: "1/1",
                    border: "1px solid var(--ink)",
                    background: r.c,
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      bottom: 0,
                      left: 0,
                      right: 0,
                      padding: "3px 6px",
                      background: "var(--ink)",
                      color: "var(--paper)",
                      fontSize: 9,
                      fontFamily: "var(--font-mono)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {i + 1} · {r.l}
                  </div>
                  <button
                    style={{
                      position: "absolute",
                      top: 4,
                      right: 4,
                      width: 18,
                      height: 18,
                      background: "var(--ink)",
                      color: "var(--paper)",
                      border: "none",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Icon name="close" size={8} />
                  </button>
                </div>
              ))}
              {[...Array(4)].map((_, i) => (
                <div
                  key={i}
                  style={{
                    aspectRatio: "1/1",
                    border: "1px dashed var(--ink-3)",
                    background: "transparent",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 4,
                  }}
                >
                  <Icon name="plus" size={14} stroke="var(--ink-4)" />
                  <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>
                    {4 + i}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 28 }}>
            <div
              className="display"
              style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em", marginBottom: 12 }}
            >
              Shape
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 8 }}>
              {ASPECTS.map((x) => {
                const on = aspect === x.r;
                return (
                  <button
                    key={x.r}
                    onClick={() => setAspect(x.r)}
                    style={{
                      padding: 14,
                      background: on ? "var(--ink)" : "#fffdf7",
                      border: "1px solid var(--ink)",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 8,
                      color: on ? "var(--banana)" : "var(--ink)",
                    }}
                  >
                    <div
                      style={{
                        width: x.w,
                        height: x.h,
                        background: on ? "var(--banana)" : "var(--paper-3)",
                        border: "1px solid currentColor",
                      }}
                    />
                    <div className="mono" style={{ fontSize: 11, fontWeight: 700 }}>
                      {x.r}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div style={{ marginTop: 28 }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 12,
              }}
            >
              <div
                className="display"
                style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em" }}
              >
                Image size
              </div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                resolution / longest edge
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
              {SIZES.map((x) => {
                const on = size === x.id;
                return (
                  <button
                    key={x.id}
                    onClick={() => setSize(x.id)}
                    style={{
                      padding: "14px 12px",
                      background: on ? "var(--ink)" : "#fffdf7",
                      border: "1px solid var(--ink)",
                      color: on ? "var(--banana)" : "var(--ink)",
                      cursor: "pointer",
                      textAlign: "center",
                    }}
                  >
                    <div
                      className="ticker"
                      style={{
                        fontSize: 26,
                        fontWeight: 900,
                        letterSpacing: "-0.03em",
                        lineHeight: 1,
                      }}
                    >
                      {x.label}
                    </div>
                    <div
                      className="mono"
                      style={{ fontSize: 10, marginTop: 6, opacity: on ? 0.8 : 0.6 }}
                    >
                      {x.note}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div
          style={{
            padding: "28px 24px",
            background: "var(--paper-2)",
            position: "sticky",
            top: 0,
            alignSelf: "flex-start",
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 10 }}
          >
            Output count
          </div>
          <div style={{ border: "1px solid var(--ink)", padding: 14, background: "#fffdf7" }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
            >
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                per generation
              </span>
              <div
                className="ticker"
                style={{ fontSize: 26, fontWeight: 900, letterSpacing: "-0.03em" }}
              >
                ×{count}
              </div>
            </div>
            <div style={{ display: "flex", gap: 4, marginTop: 8 }}>
              {[1, 2, 4, 6, 8, 12].map((n) => (
                <button
                  key={n}
                  onClick={() => setCount(n)}
                  style={{
                    flex: 1,
                    height: 32,
                    background: count === n ? "var(--banana)" : "transparent",
                    border: "1px solid var(--ink)",
                    cursor: "pointer",
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    fontWeight: 700,
                  }}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>

          <div className="hair" style={{ margin: "20px 0" }} />

          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 10 }}
          >
            Model
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {MODELS.map((m) => {
              const on = model === m.id;
              return (
                <button
                  key={m.id}
                  onClick={() => setModel(m.id)}
                  style={{
                    padding: 12,
                    background: on ? "var(--ink)" : "#fffdf7",
                    color: on ? "var(--paper)" : "var(--ink)",
                    border: "1px solid var(--ink)",
                    cursor: "pointer",
                    textAlign: "left",
                    position: "relative",
                    display: "flex",
                    gap: 12,
                    alignItems: "flex-start",
                  }}
                >
                  <ModelLogo kind={m.logo} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: 8,
                      }}
                    >
                      <span style={{ fontSize: 13, fontWeight: 700 }}>{m.name}</span>
                      <span
                        className="mono"
                        style={{
                          fontSize: 9,
                          padding: "1px 6px",
                          letterSpacing: "0.1em",
                          background: on ? "var(--banana)" : "var(--ink)",
                          color: on ? "var(--ink)" : "var(--banana)",
                        }}
                      >
                        {m.tag}
                      </span>
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        lineHeight: 1.45,
                        marginTop: 6,
                        color: on ? "var(--paper-2)" : "var(--ink-3)",
                      }}
                    >
                      {m.blurb}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          <div className="hair" style={{ margin: "20px 0" }} />

          <details>
            <summary
              className="mono caps"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                cursor: "pointer",
                outline: "none",
                userSelect: "none",
              }}
            >
              ◢ Advanced
            </summary>
            <div
              style={{
                marginTop: 12,
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              <div style={{ border: "1px solid var(--ink)", padding: 12, background: "#fffdf7" }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                  }}
                >
                  <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                    Temperature
                  </div>
                  <div
                    className="ticker"
                    style={{ fontSize: 20, fontWeight: 900, letterSpacing: "-0.03em" }}
                  >
                    {temp.toFixed(2)}
                  </div>
                </div>
                <input
                  type="range"
                  min="0"
                  max="1.5"
                  step="0.05"
                  value={temp}
                  onChange={(e) => setTemp(+e.target.value)}
                  style={{ width: "100%", marginTop: 6, accentColor: "var(--banana-deep)" }}
                />
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 10,
                    color: "var(--ink-3)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  <span>literal</span>
                  <span>chaotic</span>
                </div>
              </div>

              <div>
                <div
                  className="mono"
                  style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 4 }}
                >
                  Thinking level
                </div>
                <div style={{ display: "flex", gap: 0, border: "1px solid var(--ink)" }}>
                  {["low", "mid", "high"].map((t, i) => (
                    <button
                      key={t}
                      onClick={() => setThinking(t)}
                      style={{
                        flex: 1,
                        height: 30,
                        background: thinking === t ? "var(--banana)" : "#fffdf7",
                        border: "none",
                        borderLeft: i ? "1px solid var(--ink)" : "none",
                        cursor: "pointer",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                      }}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <div
                  className="mono"
                  style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 4 }}
                >
                  Provider (admin)
                </div>
                <select className="inp" style={{ fontFamily: "var(--font-mono)" }}>
                  <option>auto (default)</option>
                  <option>nbp-pro-a</option>
                  <option>nbp-pro-b</option>
                </select>
              </div>
            </div>
          </details>

          <div className="hair" style={{ margin: "20px 0" }} />

          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 10 }}
          >
            Bind to session
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {SESSIONS.map((s) => (
              <label
                key={s.n}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "8px 10px",
                  background: s.bound ? "var(--banana-soft)" : "#fffdf7",
                  border: "1px solid var(--ink)",
                  cursor: "pointer",
                }}
              >
                <input type="checkbox" defaultChecked={s.bound} />
                <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{s.n}</span>
                <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                  {s.c}
                </span>
              </label>
            ))}
            <button
              style={{
                padding: "8px 10px",
                background: "transparent",
                border: "1px dashed var(--ink-3)",
                cursor: "pointer",
                fontSize: 11,
                color: "var(--ink-3)",
                textAlign: "left",
              }}
            >
              + New session…
            </button>
          </div>

          <div style={{ marginTop: 28, padding: "16px 0", borderTop: "2px solid var(--ink)" }}>
            <button
              onClick={() => setShowTurnstile(true)}
              className="btn primary shadowed lg"
              style={{ width: "100%", fontSize: 15 }}
            >
              <Icon name="bolt" size={14} /> Generate ×{count}
              <span
                className="kbd"
                style={{ marginLeft: "auto", background: "var(--ink)", color: "var(--banana)" }}
              >
                ⌘↵
              </span>
            </button>
            <div
              className="mono"
              style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 8, textAlign: "center" }}
            >
              A quick human-check appears if you're over today's quota.
            </div>
          </div>
        </div>
      </div>

      <TurnstileModal
        open={showTurnstile}
        onClose={() => setShowTurnstile(false)}
        onContinue={() => setShowTurnstile(false)}
      />
    </div>
  );
}
