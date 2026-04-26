import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Icon from "./Icon.jsx";
import { Logo, Wordmark } from "./Logo.jsx";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "chart", path: "/" },
  { id: "create", label: "Create", icon: "spark", path: "/create" },
  { id: "picker", label: "Picker", icon: "star", path: "/picker" },
  { id: "history", label: "Archive", icon: "archive", path: "/archive" },
];

const BOTTOM = [
  { id: "admin", label: "Admin", icon: "user", path: "/admin" },
  { id: "settings", label: "Settings", icon: "gear", path: "/settings" },
];

export default function Sidebar({ defaultMode = "expanded" }) {
  const [mode, setMode] = useState(defaultMode);
  const isRail = mode === "rail";
  const railWidth = 56;
  const expandedWidth = 232;

  const location = useLocation();
  const navigate = useNavigate();
  const active =
    NAV.find((n) => n.path === location.pathname)?.id ||
    BOTTOM.find((n) => n.path === location.pathname)?.id;

  const expand = () => setMode("expanded");
  const collapse = () => setMode("rail");

  const NavBtn = ({ it, muted = false }) => {
    const on = active === it.id;
    return (
      <button
        onClick={(e) => {
          e.stopPropagation();
          navigate(it.path);
        }}
        title={isRail ? it.label : undefined}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: isRail ? "10px 0" : "10px 12px",
          justifyContent: isRail ? "center" : "flex-start",
          background: on ? "var(--ink)" : "transparent",
          color: on ? "var(--paper)" : muted ? "var(--ink-2)" : "var(--ink)",
          border: "none",
          cursor: "pointer",
          fontFamily: "var(--font-sans)",
          fontSize: 13,
          fontWeight: 600,
          textAlign: "left",
          position: "relative",
          width: "100%",
        }}
      >
        <Icon name={it.icon} size={muted ? 14 : 16} />
        {!isRail && <span style={{ whiteSpace: "nowrap" }}>{it.label}</span>}
        {on && !isRail && (
          <span
            style={{ marginLeft: "auto", width: 4, height: 16, background: "var(--banana)" }}
          />
        )}
        {on && isRail && (
          <span
            style={{
              position: "absolute",
              right: 0,
              top: "50%",
              transform: "translateY(-50%)",
              width: 3,
              height: 18,
              background: "var(--banana)",
            }}
          />
        )}
      </button>
    );
  };

  return (
    <div
      onClick={isRail ? expand : undefined}
      style={{
        width: isRail ? railWidth : expandedWidth,
        flexShrink: 0,
        borderRight: "1px solid var(--ink)",
        background: isRail ? "var(--ink)" : "var(--paper-2)",
        display: "flex",
        flexDirection: "column",
        transition: "width 200ms cubic-bezier(.2,.8,.2,1), background 200ms ease",
        cursor: isRail ? "pointer" : "default",
        position: "relative",
      }}
    >
      <div
        style={{
          padding: isRail ? "16px 0" : "20px 18px",
          borderBottom: isRail ? "1px solid #ffffff15" : "1px solid var(--ink)",
          display: "flex",
          alignItems: "center",
          justifyContent: isRail ? "center" : "space-between",
          gap: 10,
          position: "relative",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Logo size={isRail ? 32 : 28} />
          {!isRail && <Wordmark />}
        </div>
      </div>

      <nav
        style={{
          padding: isRail ? 8 : 12,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          flex: 1,
        }}
      >
        {NAV.map((it) => (
          <NavBtn key={it.id} it={it} />
        ))}
      </nav>

      <div
        style={{
          padding: isRail ? 8 : 12,
          borderTop: isRail ? "1px solid #ffffff15" : "1px solid var(--ink)",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {!isRail && BOTTOM.map((it) => <NavBtn key={it.id} it={it} muted />)}
        {isRail &&
          BOTTOM.map((it) => (
            <button
              key={it.id}
              onClick={(e) => {
                e.stopPropagation();
                navigate(it.path);
              }}
              title={it.label}
              style={{
                padding: "8px 0",
                justifyContent: "center",
                display: "flex",
                background: "transparent",
                color: "#ffffffaa",
                border: "none",
                cursor: "pointer",
              }}
            >
              <Icon name={it.icon} size={14} />
            </button>
          ))}
      </div>

      {!isRail && (
        <div
          style={{
            padding: "14px 16px",
            borderTop: "1px solid var(--ink)",
            background: "var(--paper-3)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <div
            style={{
              width: 28,
              height: 28,
              background: "var(--ink)",
              color: "var(--banana)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 11,
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
            }}
          >
            LX
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 700 }}>liuxi</div>
            <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              ADMIN
            </div>
          </div>
          <button
            onClick={(e) => {
              e.stopPropagation();
              collapse();
            }}
            title="Collapse sidebar"
            style={{
              width: 22,
              height: 22,
              border: "1px solid var(--ink)",
              background: "#fffdf7",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <Icon name="double-chevron-left" size={9} />
          </button>
        </div>
      )}

      {isRail && (
        <div
          style={{
            padding: "10px 0",
            borderTop: "1px solid #ffffff15",
            display: "flex",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              width: 28,
              height: 28,
              background: "var(--banana)",
              color: "var(--ink)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 10,
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              border: "1px solid var(--ink)",
            }}
          >
            LX
          </div>
        </div>
      )}
    </div>
  );
}
