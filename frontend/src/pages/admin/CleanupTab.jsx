import { Hair } from "./atoms.jsx";

const SUGGESTIONS = [
  {
    label: "Older than 7 days",
    cutoff: "2026-04-22",
    jobs: 421,
    images: 1380,
    bytes: "2.0 GB",
  },
  {
    label: "Older than 30 days",
    cutoff: "2026-03-30",
    jobs: 1820,
    images: 6210,
    bytes: "8.8 GB",
    recommended: true,
  },
  {
    label: "Older than 90 days",
    cutoff: "2026-01-29",
    jobs: 4120,
    images: 12480,
    bytes: "21.4 GB",
  },
  {
    label: "Failed jobs older than 1 day",
    cutoff: "—",
    jobs: 88,
    images: 0,
    bytes: "12 MB",
  },
  {
    label: "Cancelled jobs · any age",
    cutoff: "—",
    jobs: 142,
    images: 0,
    bytes: "—",
  },
];

export default function CleanupTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div
        style={{
          display: "flex",
          gap: 14,
          alignItems: "flex-start",
          justifyContent: "space-between",
        }}
      >
        <div>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            /api/admin/cleanup
          </div>
          <div
            className="display"
            style={{
              fontSize: 32,
              fontWeight: 800,
              letterSpacing: "-0.025em",
              marginTop: 6,
            }}
          >
            Free up{" "}
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
              disk.
            </span>
          </div>
          <div
            style={{
              fontSize: 13,
              color: "var(--ink-3)",
              marginTop: 6,
              fontStyle: "italic",
              fontFamily: "var(--font-display)",
            }}
          >
            DB rows flag DELETED first; then async rm -rf data/jobs/&lt;hash&gt;. SSE
            task_deleted broadcasts.
          </div>
        </div>
        <div
          style={{
            minWidth: 240,
            padding: 14,
            border: "1px solid var(--ink)",
            background: "#fffdf7",
          }}
        >
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            DATA/JOBS DISK
          </div>
          <div
            className="ticker"
            style={{
              fontSize: 36,
              fontWeight: 900,
              letterSpacing: "-0.04em",
              lineHeight: 1,
              marginTop: 6,
            }}
          >
            13.2{" "}
            <span className="mono" style={{ fontSize: 14, color: "var(--ink-3)" }}>
              GB
            </span>
          </div>
          <div
            style={{
              height: 6,
              background: "var(--paper-3)",
              marginTop: 10,
              position: "relative",
            }}
          >
            <div
              style={{
                position: "absolute",
                inset: 0,
                width: "8%",
                background: "var(--ink)",
              }}
            />
          </div>
          <div
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 6 }}
          >
            of 180 GB · 8% used · refreshed 4m ago
          </div>
        </div>
      </div>

      <div>
        <div
          className="mono caps"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            letterSpacing: "0.16em",
            marginBottom: 10,
          }}
        >
          SUGGESTIONS
        </div>
        <Hair thick />
        <div
          style={{
            marginTop: 12,
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {SUGGESTIONS.map((s) => (
            <div
              key={s.label}
              style={{
                border: "1px solid var(--ink)",
                background: s.recommended ? "var(--banana-soft)" : "#fffdf7",
                padding: "14px 18px",
                display: "grid",
                gridTemplateColumns: "minmax(220px, 1.4fr) 1fr 1fr 1fr 160px",
                gap: 16,
                alignItems: "center",
                position: "relative",
              }}
            >
              {s.recommended && (
                <div
                  style={{
                    position: "absolute",
                    top: -1,
                    left: -1,
                    padding: "2px 8px",
                    background: "var(--ink)",
                    color: "var(--banana)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    fontWeight: 700,
                    letterSpacing: "0.14em",
                  }}
                >
                  RECOMMENDED
                </div>
              )}
              <div>
                <div
                  style={{
                    fontSize: 15,
                    fontWeight: 700,
                    marginTop: s.recommended ? 12 : 0,
                  }}
                >
                  {s.label}
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}
                >
                  cutoff · {s.cutoff}
                </div>
              </div>
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                  JOBS
                </div>
                <div
                  className="ticker"
                  style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.02em" }}
                >
                  {s.jobs.toLocaleString()}
                </div>
              </div>
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                  IMAGES
                </div>
                <div
                  className="ticker"
                  style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.02em" }}
                >
                  {s.images.toLocaleString()}
                </div>
              </div>
              <div>
                <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                  FREES
                </div>
                <div
                  className="ticker"
                  style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.02em" }}
                >
                  {s.bytes}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                <button className="btn sm">Dry-run</button>
                <button className="btn sm ink">Run cleanup</button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div
          className="mono caps"
          style={{
            fontSize: 10,
            color: "var(--ink-3)",
            letterSpacing: "0.16em",
            marginBottom: 10,
          }}
        >
          CUSTOM RULES
        </div>
        <Hair thick />
        <div
          style={{
            marginTop: 12,
            padding: 16,
            border: "1px solid var(--ink)",
            background: "#fffdf7",
          }}
        >
          <div
            style={{
              display: "flex",
              gap: 10,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <select
              className="inp"
              defaultValue="older"
              style={{ width: 200, fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="older">kind · older_than_days</option>
              <option>kind · status_only</option>
              <option>kind · user_id</option>
            </select>
            <input
              className="inp"
              placeholder="days"
              defaultValue="30"
              style={{ width: 100, fontFamily: "var(--font-mono)" }}
            />
            <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
              · statuses ·
            </span>
            {["SUCCEEDED", "FAILED", "CANCELLED", "DELETED"].map((s, i) => (
              <span
                key={s}
                className={`chip ${i < 2 ? "solid" : ""}`}
                style={{ cursor: "pointer" }}
              >
                {s}
              </span>
            ))}
            <div style={{ flex: 1 }} />
            <button className="btn sm">+ Add rule</button>
          </div>
          <div
            style={{
              marginTop: 14,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <label
              className="mono"
              style={{
                fontSize: 12,
                color: "var(--ink-2)",
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <input type="checkbox" defaultChecked /> exempt starred=true images
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn">Dry-run</button>
              <button className="btn ink shadowed">Execute cleanup</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
