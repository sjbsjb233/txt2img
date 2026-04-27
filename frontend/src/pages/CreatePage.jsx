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
    blurb: "Best for editorial, photoreal, brand. Strong text & composition control.",
  },
  {
    id: "flash",
    name: "Flash Diffuser",
    tag: "FAST",
    logo: "flash",
    blurb: "Snappy generations. Good for sketching directions before committing.",
  },
  {
    id: "draft",
    name: "Draft Lite",
    tag: "DRAFT",
    logo: "draft",
    blurb: "Lightweight rough pass. Tiny preview thumbnails to validate composition first.",
  },
];

const DEFAULT_REFS = [
  { c: "#E8D5B0", l: "banana_01.jpg" },
  { c: "#4A5D3F", l: "leaf.png" },
  { c: "#C97B5E", l: "clay.jpg" },
];

const ASPECTS = [
  { r: "1:1", w: 26, h: 26 },
  { r: "4:3", w: 30, h: 22 },
  { r: "3:4", w: 22, h: 30 },
  { r: "16:9", w: 34, h: 20 },
  { r: "9:16", w: 18, h: 32 },
  { r: "21:9", w: 36, h: 16 },
];

const SIZES = [
  { id: "512", label: "512", note: "preview" },
  { id: "1K", label: "1K", note: "balanced" },
  { id: "2K", label: "2K", note: "print" },
  { id: "4K", label: "4K", note: "max" },
];

const SESSIONS = [
  { n: "Editorial Cover", bound: true, c: 24, when: "updated 12m ago" },
  { n: "Q2 Brand Refresh", bound: false, c: 56, when: "updated 3d ago" },
  { n: "Client · Acme", bound: false, c: 12, when: "updated 1w ago" },
];

export default function CreatePage() {
  const [model, setModel] = useState("chatgpt");
  const [aspect, setAspect] = useState("1:1");
  const [size, setSize] = useState("1K");
  const [count, setCount] = useState(4);
  const [temp, setTemp] = useState(0.7);
  const [thinking, setThinking] = useState("mid");
  const [showTurnstile, setShowTurnstile] = useState(false);
  const [refs, setRefs] = useState(DEFAULT_REFS);
  const [prompt, setPrompt] = useState(
    "A still-life of three overripe bananas on a marble slab, flemish painting, warm window light, shallow depth of field, 35mm"
  );

  const isEmpty = refs.length === 0;

  return (
    <div
      style={{
        flex: 1,
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
      }}
    >
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
            <button
              className="btn sm ghost"
              onClick={() => {
                setPrompt("");
                setRefs([]);
              }}
            >
              Clear
            </button>
          </>
        }
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 360px",
          flex: 1,
          minHeight: 0,
        }}
      >
        <div
          style={{
            padding: "20px 32px 24px",
            borderRight: "1px solid var(--ink)",
            display: "flex",
            flexDirection: "column",
            gap: 18,
            minHeight: 0,
            overflowY: "auto",
          }}
        >
          <div
            style={{
              border: "1px solid var(--ink)",
              background: "#fffdf7",
              flex: 1,
              minHeight: 180,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 14px",
                borderBottom: "1px solid var(--ink)",
                background: "var(--paper-2)",
                flexShrink: 0,
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
              style={{
                width: "100%",
                flex: 1,
                border: "none",
                outline: "none",
                padding: "16px 22px",
                fontFamily: "var(--font-display)",
                fontSize: 20,
                lineHeight: 1.4,
                background: "transparent",
                color: "var(--ink)",
                letterSpacing: "-0.01em",
                resize: "vertical",
                minHeight: 110,
                display: "block",
              }}
            />
            <div
              style={{
                padding: "6px 14px",
                borderTop: "1px dashed var(--rule-2)",
                background: "var(--paper)",
                display: "flex",
                justifyContent: "space-between",
                fontSize: 10,
                color: "var(--ink-3)",
                flexShrink: 0,
              }}
            >
              <span className="mono">
                Tip: mention <b>subject · style · light · composition · lens</b>
              </span>
              <span className="mono">⌘↵ to generate</span>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", flexShrink: 0 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: 10,
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span
                  className="display"
                  style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em" }}
                >
                  Reference images
                </span>
                <span className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                  {refs.length} / 14 · OPTIONAL
                </span>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="chip" onClick={() => setRefs([])}>
                  <Icon name="close" size={9} /> CLEAR
                </button>
                <button className="chip">
                  <Icon name="upload" size={10} /> UPLOAD
                </button>
                <button className="chip">PASTE ⌘V</button>
              </div>
            </div>

            {isEmpty ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 8 }}>
                <div
                  style={{
                    gridColumn: "1 / 5",
                    aspectRatio: "4/1",
                    border: "1px dashed var(--ink)",
                    background:
                      "repeating-linear-gradient(45deg, transparent 0 10px, rgba(0,0,0,0.02) 10px 11px), #fffdf7",
                    display: "flex",
                    alignItems: "center",
                    gap: 14,
                    padding: "0 18px",
                    position: "relative",
                  }}
                >
                  <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                    {["#E8D5B0", "#C9B894", "#A89878"].map((c, i) => (
                      <div
                        key={i}
                        style={{
                          width: 28,
                          height: 28,
                          background: c,
                          border: "1px solid var(--ink)",
                          transform: `rotate(${(i - 1) * 4}deg)`,
                          boxShadow: "1px 1px 0 var(--ink)",
                        }}
                      />
                    ))}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      className="display"
                      style={{
                        fontSize: 16,
                        fontWeight: 700,
                        letterSpacing: "-0.02em",
                        color: "var(--ink)",
                        lineHeight: 1.15,
                      }}
                    >
                      Drop reference imagery{" "}
                      <span style={{ fontStyle: "italic", color: "var(--ink-3)" }}>— or skip.</span>
                    </div>
                    <div
                      className="mono"
                      style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 4 }}
                    >
                      Up to 14 images. Optional — pure-text prompts work great.
                    </div>
                  </div>
                  <button
                    className="btn sm"
                    style={{ flexShrink: 0 }}
                    onClick={() => setRefs(DEFAULT_REFS)}
                  >
                    <Icon name="upload" size={11} /> Choose files
                  </button>
                </div>
                {[...Array(3)].map((_, i) => (
                  <div
                    key={i}
                    style={{
                      aspectRatio: "1/1",
                      border: "1px dashed var(--ink-4)",
                      background: "transparent",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 4,
                      opacity: 0.5,
                    }}
                  >
                    <Icon name="plus" size={12} stroke="var(--ink-4)" />
                    <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>
                      {i + 1}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 8 }}>
                {refs.map((r, i) => (
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
                      onClick={() => setRefs(refs.filter((_, j) => j !== i))}
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
                {[...Array(Math.max(0, 7 - refs.length))].map((_, i) => (
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
                      {refs.length + 1 + i}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div style={{ flexShrink: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 10,
              }}
            >
              <div
                className="display"
                style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em" }}
              >
                Model
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                which engine renders this
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
              {MODELS.map((m) => {
                const on = model === m.id;
                return (
                  <button
                    key={m.id}
                    onClick={() => setModel(m.id)}
                    style={{
                      padding: 10,
                      background: on ? "var(--ink)" : "#fffdf7",
                      color: on ? "var(--paper)" : "var(--ink)",
                      border: "1px solid var(--ink)",
                      cursor: "pointer",
                      textAlign: "left",
                      position: "relative",
                      display: "flex",
                      gap: 10,
                      alignItems: "flex-start",
                    }}
                  >
                    <ModelLogo kind={m.logo} size={22} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 6,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 12,
                            fontWeight: 700,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {m.name}
                        </span>
                        <span
                          className="mono"
                          style={{
                            fontSize: 8,
                            padding: "1px 5px",
                            letterSpacing: "0.1em",
                            flexShrink: 0,
                            background: on ? "var(--banana)" : "var(--ink)",
                            color: on ? "var(--ink)" : "var(--banana)",
                          }}
                        >
                          {m.tag}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 10,
                          lineHeight: 1.4,
                          marginTop: 4,
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
          </div>

          <div style={{ flexShrink: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 10,
              }}
            >
              <div
                className="display"
                style={{ fontSize: 20, fontWeight: 700, letterSpacing: "-0.02em" }}
              >
                Bind to session
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                group this run with related work
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
              {SESSIONS.map((s) => (
                <label
                  key={s.n}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                    padding: "10px 11px",
                    background: s.bound ? "var(--banana-soft)" : "#fffdf7",
                    border: "1px solid var(--ink)",
                    cursor: "pointer",
                    minWidth: 0,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      minWidth: 0,
                    }}
                  >
                    <input type="checkbox" defaultChecked={s.bound} style={{ flexShrink: 0 }} />
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        flex: 1,
                        minWidth: 0,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {s.n}
                    </span>
                    <span
                      className="mono"
                      style={{ fontSize: 9, color: "var(--ink-3)", flexShrink: 0 }}
                    >
                      {s.c}
                    </span>
                  </div>
                  <span
                    className="mono"
                    style={{ fontSize: 9, color: "var(--ink-4)", paddingLeft: 20 }}
                  >
                    {s.when}
                  </span>
                </label>
              ))}
              <button
                style={{
                  padding: "10px 11px",
                  background: "transparent",
                  border: "1px dashed var(--ink-3)",
                  cursor: "pointer",
                  fontSize: 11,
                  color: "var(--ink-3)",
                  textAlign: "left",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  justifyContent: "center",
                }}
              >
                <span style={{ fontWeight: 600 }}>+ New session</span>
                <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>
                  start fresh
                </span>
              </button>
            </div>
          </div>
        </div>

        <div
          style={{
            background: "var(--paper-2)",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: "auto",
              padding: "20px 22px",
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 8 }}
            >
              Output count
            </div>
            <div style={{ border: "1px solid var(--ink)", padding: 12, background: "#fffdf7" }}>
              <div
                style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
              >
                <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                  per generation
                </span>
                <div
                  className="ticker"
                  style={{ fontSize: 24, fontWeight: 900, letterSpacing: "-0.03em" }}
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
                      height: 30,
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

            <div className="hair" style={{ margin: "18px 0" }} />

            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 10 }}
            >
              Shape
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
              {ASPECTS.map((x) => {
                const on = aspect === x.r;
                return (
                  <button
                    key={x.r}
                    onClick={() => setAspect(x.r)}
                    style={{
                      padding: "10px 8px",
                      background: on ? "var(--ink)" : "#fffdf7",
                      border: "1px solid var(--ink)",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 6,
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
                    <div className="mono" style={{ fontSize: 10, fontWeight: 700 }}>
                      {x.r}
                    </div>
                  </button>
                );
              })}
            </div>

            <div className="hair" style={{ margin: "18px 0" }} />

            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 10,
              }}
            >
              <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                Image size
              </div>
              <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                longest edge
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
              {SIZES.map((x) => {
                const on = size === x.id;
                return (
                  <button
                    key={x.id}
                    onClick={() => setSize(x.id)}
                    style={{
                      padding: "10px 6px",
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
                        fontSize: 20,
                        fontWeight: 900,
                        letterSpacing: "-0.03em",
                        lineHeight: 1,
                      }}
                    >
                      {x.label}
                    </div>
                    <div
                      className="mono"
                      style={{ fontSize: 9, marginTop: 4, opacity: on ? 0.8 : 0.6 }}
                    >
                      {x.note}
                    </div>
                  </button>
                );
              })}
            </div>

            <div className="hair" style={{ margin: "18px 0" }} />

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
              <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 14 }}>
                <div
                  style={{ border: "1px solid var(--ink)", padding: 12, background: "#fffdf7" }}
                >
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
          </div>

          <div
            style={{
              padding: "14px 22px 18px",
              borderTop: "2px solid var(--ink)",
              background: "var(--paper-2)",
              flexShrink: 0,
            }}
          >
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
