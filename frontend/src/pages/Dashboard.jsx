import Icon from "../components/Icon.jsx";

function ProgBar({ value, max = 100, color = "var(--ink)", track = "var(--paper-3)", height = 8 }) {
  return (
    <div style={{ height, background: track, position: "relative" }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          width: `${Math.min(100, (value / max) * 100)}%`,
          background: color,
        }}
      />
    </div>
  );
}

const IN_FLIGHT = [
  {
    kind: "batch",
    name: "印象派系列",
    color: "var(--banana-deep)",
    track: "var(--banana-soft)",
    value: 14,
    max: 20,
    label: "14/20",
  },
  {
    kind: "single",
    name: "marble still life",
    color: "var(--banana-deep)",
    track: "var(--banana-soft)",
    value: 0,
    max: 1,
    label: "queued",
  },
  {
    kind: "batch",
    name: "client deliverables",
    color: "var(--bad)",
    track: "#e8bdb5",
    value: 0,
    max: 1,
    label: "2 failed",
    failed: true,
  },
];

const PENDING = [
  { c: "#a4d8c3", name: "Q2 brand refresh", meta: "56 images · 3 days old" },
  { c: "#e8a98a", name: "Client · Acme", meta: "12 images · yesterday" },
  { c: "#b8a8e0", name: "Marble study", meta: "8 images · 2h ago" },
];

const PICKS = [
  { c: "#9bdac5", r: 5 },
  { c: "#e8a98a", r: 4 },
  { c: "#1c1b18", r: 5 },
  { c: "#f0c2db", r: 4 },
  { c: "#bedb98", r: 3 },
  { c: "#f5c862", r: 5 },
];

export default function Dashboard() {
  return (
    <div style={{ flex: 1, overflowY: "auto", background: "var(--paper)" }}>
      <div style={{ maxWidth: 1080, margin: "0 auto", padding: "44px 56px 80px" }}>
        <div
          className="mono caps"
          style={{ fontSize: 11, color: "var(--ink-3)", letterSpacing: "0.18em" }}
        >
          TXT2IMG · WED 24 APR · GOOD AFTERNOON, LIUXI
        </div>

        <h1
          className="display"
          style={{
            fontSize: 84,
            fontWeight: 900,
            letterSpacing: "-0.045em",
            lineHeight: 0.95,
            margin: "32px 0 0",
          }}
        >
          Pick up where you
          <br />
          <span style={{ fontStyle: "italic", color: "var(--banana-deep)", fontWeight: 900 }}>
            left off.
          </span>
        </h1>

        <section
          style={{
            marginTop: 48,
            background: "var(--ink)",
            color: "var(--paper)",
            display: "grid",
            gridTemplateColumns: "260px 1fr",
            border: "1px solid var(--ink)",
          }}
        >
          <div
            style={{
              background: "#1a1916",
              padding: 18,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              position: "relative",
            }}
          >
            <div
              style={{
                width: "100%",
                aspectRatio: "16/10",
                background: "#37352f",
                border: "1px solid #4a463f",
                position: "relative",
                backgroundImage:
                  "repeating-linear-gradient(135deg,#3a3631,#3a3631 6px,#403c36 6px,#403c36 12px)",
              }}
            >
              <span
                className="mono caps"
                style={{
                  position: "absolute",
                  top: 6,
                  left: 8,
                  fontSize: 9,
                  color: "#9c9488",
                }}
              >
                session preview
              </span>
            </div>
          </div>
          <div
            style={{
              padding: "26px 30px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.16em" }}
            >
              SESSION · EDITORIAL COVER · 24 IMAGES
            </div>
            <h2
              className="display"
              style={{
                fontSize: 38,
                fontWeight: 800,
                letterSpacing: "-0.03em",
                margin: "12px 0 6px",
                color: "var(--paper)",
              }}
            >
              15 unreviewed
            </h2>
            <div
              style={{
                fontFamily: "var(--font-display)",
                fontStyle: "italic",
                fontSize: 15,
                color: "var(--ink-4)",
              }}
            >
              started 18 minutes ago · resume Picker
            </div>
            <div style={{ marginTop: 20, display: "flex", gap: 10 }}>
              <button
                className="btn primary"
                style={{
                  height: 44,
                  padding: "0 22px",
                  fontFamily: "var(--font-mono)",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  fontSize: 12,
                }}
              >
                Resume <span style={{ marginLeft: 6 }}>›</span>
              </button>
              <button
                className="btn"
                style={{
                  height: 44,
                  padding: "0 22px",
                  background: "transparent",
                  color: "var(--paper)",
                  borderColor: "var(--paper-2)",
                  fontFamily: "var(--font-mono)",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  fontSize: 12,
                }}
              >
                Details
              </button>
            </div>
          </div>
        </section>

        <section
          style={{
            marginTop: 48,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 36,
          }}
        >
          <div>
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
            >
              IN FLIGHT · 3 RUNNING
            </div>
            <div style={{ marginTop: 10, height: 1, background: "var(--ink)" }} />

            {IN_FLIGHT.map((j, i) => (
              <div key={i} style={{ marginTop: 16 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span
                    className="mono"
                    style={{
                      fontSize: 12,
                      fontWeight: 700,
                      color: j.failed ? "var(--bad)" : "var(--ink)",
                    }}
                  >
                    {j.kind}
                  </span>
                  <span
                    className="mono"
                    style={{ fontSize: 13, color: j.failed ? "var(--bad)" : "var(--ink-2)" }}
                  >
                    · {j.name}
                  </span>
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 80px",
                    gap: 12,
                    alignItems: "center",
                    marginTop: 6,
                  }}
                >
                  <ProgBar value={j.value} max={j.max} color={j.color} track={j.track} height={8} />
                  <span
                    className="mono"
                    style={{ fontSize: 11, color: "var(--ink-3)", textAlign: "right" }}
                  >
                    {j.label}
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div>
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
            >
              PENDING REVIEW · 3 SESSIONS
            </div>
            <div style={{ marginTop: 10, height: 1, background: "var(--ink)" }} />

            <div
              style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}
            >
              {PENDING.map((s, i) => (
                <div
                  key={i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "44px 1fr 20px",
                    gap: 14,
                    alignItems: "center",
                    padding: "12px 14px",
                    border: "1px solid var(--ink)",
                    background: "#fffdf7",
                    cursor: "pointer",
                  }}
                >
                  <div
                    style={{
                      width: 44,
                      height: 44,
                      background: s.c,
                      border: "1px solid var(--ink)",
                    }}
                  />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700 }}>{s.name}</div>
                    <div
                      className="mono"
                      style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 2 }}
                    >
                      {s.meta}
                    </div>
                  </div>
                  <Icon name="arrow" size={14} />
                </div>
              ))}
            </div>
          </div>
        </section>

        <section style={{ marginTop: 56 }}>
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            RECENT PICKS · YOUR LAST 7 KEEPERS
          </div>
          <div style={{ marginTop: 10, height: 1, background: "var(--ink)" }} />

          <div
            style={{
              marginTop: 18,
              display: "grid",
              gridTemplateColumns: "repeat(7, 1fr)",
              gap: 12,
            }}
          >
            {PICKS.map((p, i) => (
              <div key={i}>
                <div
                  style={{
                    aspectRatio: "1/1",
                    background: p.c,
                    border: "1px solid var(--ink)",
                  }}
                />
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    color: "var(--banana-deep)",
                    marginTop: 8,
                    letterSpacing: 1,
                  }}
                >
                  {"★".repeat(p.r)}
                </div>
              </div>
            ))}
            <a
              style={{
                aspectRatio: "1/1",
                border: "1px dashed var(--ink-3)",
                background: "transparent",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--ink-3)",
                textDecoration: "none",
                cursor: "pointer",
              }}
            >
              see all →
            </a>
          </div>
        </section>

        <section style={{ marginTop: 56 }}>
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            QUICK START
          </div>
          <div style={{ marginTop: 10, height: 1, background: "var(--ink)" }} />

          <div
            style={{
              marginTop: 18,
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr",
              gap: 14,
            }}
          >
            <button
              style={{
                padding: "18px 20px",
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                cursor: "pointer",
                textAlign: "left",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div style={{ fontSize: 16, fontWeight: 700 }}>+ Blank create</div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>⌘N</div>
            </button>
            <button
              style={{
                padding: "18px 20px",
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                cursor: "pointer",
                textAlign: "left",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div style={{ fontSize: 16, fontWeight: 700 }}>↻ Repeat last batch</div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                印象派系列 · 20 imgs
              </div>
            </button>
            <button
              style={{
                padding: "18px 20px",
                border: "1px solid var(--ink)",
                background: "var(--ink)",
                color: "var(--paper)",
                cursor: "pointer",
                textAlign: "left",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div style={{ fontSize: 16, fontWeight: 700, color: "var(--banana)" }}>
                ⚡ Resume drafted prompt
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>
                "flemish painting marble..."
              </div>
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
