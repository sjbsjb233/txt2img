import Icon from "../../components/Icon.jsx";

const LIST = [
  {
    id: "ann_aBcD1234ef",
    title: "Scheduled maintenance",
    kind: "text",
    status: "live",
    audience: "all",
    reach: 142,
    read: 36,
    priority: 5,
    starts: "2026-04-28 02:00",
    ends: "2026-04-28 03:00",
    content:
      "We'll be down on Sunday 02:00–03:00 UTC+8. Save your work before then.",
  },
  {
    id: "ann_xY2pZ7Lq3v",
    title: "Nano Banana Pro is here",
    kind: "image",
    status: "live",
    audience: "tier",
    audienceList: ["vip", "premium"],
    reach: 32,
    read: 28,
    priority: 8,
    starts: "2026-04-25 09:00",
    ends: null,
    content: "/announcements/ann_xY2pZ7Lq3v/cover.png",
  },
  {
    id: "ann_dRaFt0001p",
    title: "Q3 pricing changes",
    kind: "text",
    status: "draft",
    audience: "all",
    reach: 142,
    read: 0,
    priority: 3,
    starts: "2026-05-15 09:00",
    ends: null,
    content: "Premium tier hard quota increasing to 120/day…",
  },
];

export default function AnnouncementsTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: 16,
        }}
      >
        <div>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            /api/admin/announcements
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
            Talk to{" "}
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
              users.
            </span>
          </div>
        </div>
        <button className="btn primary shadowed">
          <Icon name="plus" size={13} />
          Compose
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {LIST.map((a) => (
            <div
              key={a.id}
              style={{
                border: "1px solid var(--ink)",
                background: a.status === "draft" ? "var(--paper-2)" : "#fffdf7",
                padding: 16,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: 12,
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      flexWrap: "wrap",
                    }}
                  >
                    <span
                      className="display"
                      style={{
                        fontSize: 18,
                        fontWeight: 800,
                        letterSpacing: "-0.015em",
                      }}
                    >
                      {a.title}
                    </span>
                    {a.status === "live" && <span className="chip ok">LIVE</span>}
                    {a.status === "draft" && <span className="chip">DRAFT</span>}
                    <span className="chip">P{a.priority}</span>
                    <span
                      className="chip"
                      style={{
                        background:
                          a.kind === "image" ? "var(--banana-soft)" : "transparent",
                      }}
                    >
                      {a.kind}
                    </span>
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}
                  >
                    {a.id}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 4 }}>
                  <button className="btn sm">Edit</button>
                  <button className="btn sm">Delete</button>
                </div>
              </div>

              <div
                style={{
                  marginTop: 10,
                  fontSize: 13,
                  color: "var(--ink-2)",
                  lineHeight: 1.5,
                }}
              >
                {a.kind === "text" ? (
                  `"${a.content}"`
                ) : (
                  <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span
                      style={{
                        width: 36,
                        height: 36,
                        background: "var(--banana)",
                        border: "1px solid var(--ink)",
                        display: "inline-block",
                      }}
                    />
                    <span className="mono" style={{ fontSize: 11 }}>
                      {a.content}
                    </span>
                  </span>
                )}
              </div>

              <div
                style={{
                  marginTop: 12,
                  paddingTop: 10,
                  borderTop: "1px solid var(--rule)",
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: 10,
                }}
              >
                <div>
                  <div
                    className="mono caps"
                    style={{ fontSize: 9, color: "var(--ink-3)" }}
                  >
                    AUDIENCE
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 11, fontWeight: 700, marginTop: 2 }}
                  >
                    {a.audience === "all"
                      ? "all"
                      : a.audience === "tier"
                        ? a.audienceList.join(", ")
                        : "user_list"}
                  </div>
                </div>
                <div>
                  <div
                    className="mono caps"
                    style={{ fontSize: 9, color: "var(--ink-3)" }}
                  >
                    REACH
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 11, fontWeight: 700, marginTop: 2 }}
                  >
                    {a.reach} users
                  </div>
                </div>
                <div>
                  <div
                    className="mono caps"
                    style={{ fontSize: 9, color: "var(--ink-3)" }}
                  >
                    READ
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 11, fontWeight: 700, marginTop: 2 }}
                  >
                    {a.read} ({Math.round((a.read / a.reach) * 100)}%)
                  </div>
                </div>
                <div>
                  <div
                    className="mono caps"
                    style={{ fontSize: 9, color: "var(--ink-3)" }}
                  >
                    WINDOW
                  </div>
                  <div className="mono" style={{ fontSize: 10, marginTop: 2 }}>
                    {a.starts}
                    <br />
                    {a.ends ? `→ ${a.ends}` : "→ ∞"}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div
          style={{
            border: "1px solid var(--ink)",
            background: "#fffdf7",
            padding: 18,
            alignSelf: "flex-start",
            boxShadow: "5px 5px 0 var(--ink)",
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            NEW ANNOUNCEMENT
          </div>
          <div
            style={{
              marginTop: 12,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div>
              <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                TITLE
              </div>
              <input
                className="inp"
                placeholder="Scheduled maintenance"
                style={{ marginTop: 4 }}
              />
            </div>
            <div>
              <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                CONTENT KIND
              </div>
              <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                {["text", "image", "react"].map((k, i) => (
                  <span
                    key={k}
                    className={`chip ${i === 0 ? "solid" : ""}`}
                    style={{ cursor: "pointer", opacity: k === "react" ? 0.4 : 1 }}
                  >
                    {k}
                    {k === "react" ? " · v1.1" : ""}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                CONTENT
              </div>
              <textarea
                className="inp"
                placeholder="..."
                style={{ marginTop: 4, height: 96 }}
              />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <div
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  AUDIENCE
                </div>
                <select
                  className="inp"
                  defaultValue="all"
                  style={{
                    marginTop: 4,
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                  }}
                >
                  <option>all</option>
                  <option>tier</option>
                  <option>user_list</option>
                </select>
              </div>
              <div>
                <div
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  PRIORITY
                </div>
                <input
                  className="inp"
                  defaultValue="5"
                  type="number"
                  style={{ marginTop: 4, fontFamily: "var(--font-mono)" }}
                />
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <div
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  STARTS AT
                </div>
                <input
                  className="inp"
                  defaultValue="2026-04-28 02:00"
                  style={{ marginTop: 4, fontFamily: "var(--font-mono)" }}
                />
              </div>
              <div>
                <div
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  ENDS AT
                </div>
                <input
                  className="inp"
                  placeholder="∞ leave blank for no end"
                  style={{ marginTop: 4, fontFamily: "var(--font-mono)" }}
                />
              </div>
            </div>
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
              <input type="checkbox" defaultChecked /> dismissable
            </label>
            <div
              style={{
                display: "flex",
                gap: 8,
                justifyContent: "flex-end",
                marginTop: 6,
              }}
            >
              <button className="btn">Save draft</button>
              <button className="btn primary shadowed">Publish</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
