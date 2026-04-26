export default function TopBar({ title, subtitle, right, crumb, left }) {
  return (
    <div
      style={{
        padding: "28px 36px 20px",
        borderBottom: "1px solid var(--ink)",
        background: "var(--paper)",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-end",
        gap: 20,
      }}
    >
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        {left}
        <div>
          {crumb && (
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 8 }}
            >
              {crumb}
            </div>
          )}
          <h1
            className="display"
            style={{
              fontSize: 42,
              fontWeight: 900,
              letterSpacing: "-0.035em",
              margin: 0,
              lineHeight: 1,
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <div
              style={{
                fontSize: 14,
                color: "var(--ink-3)",
                marginTop: 10,
                maxWidth: 620,
                lineHeight: 1.5,
              }}
            >
              {subtitle}
            </div>
          )}
        </div>
      </div>
      {right && (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>{right}</div>
      )}
    </div>
  );
}
