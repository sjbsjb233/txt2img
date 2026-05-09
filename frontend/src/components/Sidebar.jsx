import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Icon from "./Icon.jsx";
import { Logo, Wordmark } from "./Logo.jsx";
import { logout as logoutFlow, useAuth } from "../store/auth.js";
import { usePreferences } from "../store/preferences.js";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "chart", path: "/dashboard" },
  { id: "create", label: "Create", icon: "spark", path: "/create" },
  { id: "batch", label: "Batch", icon: "layers", path: "/batch" },
  { id: "picker", label: "Picker", icon: "star", path: "/picker" },
  { id: "history", label: "Archive", icon: "archive", path: "/archive" },
];

// `admin` is only rendered when the current user's role === "admin".
// `settings` is open to everyone.
const BOTTOM = [
  { id: "admin", label: "Admin", icon: "user", path: "/admin", adminOnly: true },
  { id: "settings", label: "Settings", icon: "gear", path: "/settings" },
];

// Device-local override of the sidebar mode. Once the user clicks
// expand/collapse, we cache their choice here so a refresh (or a new
// tab) keeps the same state, regardless of the synced default.
const SIDEBAR_LOCAL_KEY = "txt2img_sidebar_mode";

function readSidebarLocal() {
  try {
    const raw = localStorage.getItem(SIDEBAR_LOCAL_KEY);
    if (raw === "expanded" || raw === "rail") return raw;
  } catch {
    /* localStorage unavailable */
  }
  return null;
}

function writeSidebarLocal(mode) {
  try {
    localStorage.setItem(SIDEBAR_LOCAL_KEY, mode);
  } catch {
    /* localStorage unavailable */
  }
}

export default function Sidebar({ defaultMode }) {
  // Sidebar default comes from the user's synced preference; the prop
  // remains as a manual override so tests / Storybook can pin it. A
  // device-local override (set by a previous expand/collapse click)
  // wins over the synced default so refreshes preserve the user's
  // current choice.
  const { prefs } = usePreferences();
  const localOverride = readSidebarLocal();
  const initialMode =
    defaultMode ||
    localOverride ||
    prefs?.appearance?.sidebar_default ||
    "expanded";
  const [mode, setMode] = useState(initialMode);
  const [hasUserToggled, setHasUserToggled] = useState(localOverride != null);
  // Sync down updates to the saved preference unless the user has
  // explicitly toggled the sidebar (this session or a prior one) —
  // once they touch it, we stop overwriting their choice.
  useEffect(() => {
    if (hasUserToggled) return;
    const next = prefs?.appearance?.sidebar_default;
    if (next && next !== mode) setMode(next);
  }, [prefs?.appearance?.sidebar_default, hasUserToggled, mode]);

  const isRail = mode === "rail";
  const railWidth = 56;
  const expandedWidth = 232;

  const location = useLocation();
  const navigate = useNavigate();
  const { user, isAdmin } = useAuth();
  const bottomItems = BOTTOM.filter((it) => !it.adminOnly || isAdmin);
  const active =
    NAV.find((n) => n.path === location.pathname)?.id ||
    bottomItems.find((n) => n.path === location.pathname)?.id;

  const expand = () => {
    setMode("expanded");
    setHasUserToggled(true);
    writeSidebarLocal("expanded");
  };
  const collapse = () => {
    setMode("rail");
    setHasUserToggled(true);
    writeSidebarLocal("rail");
  };

  const logout = async () => {
    await logoutFlow(); // best-effort POST + clearAuth + notify
    navigate("/login", { replace: true });
  };

  const displayName = user?.display_name || user?.username || "guest";
  const initials = (displayName || "??").slice(0, 2).toUpperCase();

  const NavBtn = ({ it, muted = false }) => {
    const on = active === it.id;
    const railColor = on ? "var(--paper)" : "#e8e1d180";
    const expandedColor = on ? "var(--paper)" : muted ? "var(--ink-2)" : "var(--ink)";
    return (
      <button
        onClick={(e) => {
          e.stopPropagation();
          navigate(it.path);
        }}
        title={isRail ? it.label : undefined}
        className={isRail ? "nav-btn nav-btn-rail" : "nav-btn nav-btn-expanded"}
        data-active={on ? "true" : "false"}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: isRail ? "10px 0" : "10px 12px",
          justifyContent: isRail ? "center" : "flex-start",
          background: on ? "var(--ink)" : "transparent",
          color: isRail ? railColor : expandedColor,
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
        <Icon name={it.icon} size={isRail ? 16 : muted ? 14 : 16} />
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
      style={{
        width: isRail ? railWidth : expandedWidth,
        flexShrink: 0,
        borderRight: "1px solid var(--ink)",
        background: isRail ? "var(--ink)" : "var(--paper-2)",
        display: "flex",
        flexDirection: "column",
        transition: "width 220ms cubic-bezier(.22,.85,.22,1), background 220ms ease",
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
        {bottomItems.map((it) => (
          <NavBtn key={it.id} it={it} muted />
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
            {initials}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 700,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {displayName}
            </div>
            <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              @{user?.username || "guest"}
            </div>
          </div>
          <button
            onClick={logout}
            title="Sign out"
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
            <Icon name="logout" size={11} />
          </button>
          <button
            onClick={collapse}
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
        <button
          onClick={expand}
          title="Expand sidebar"
          className="rail-foot"
          style={{
            padding: "12px 0",
            borderTop: "1px solid #ffffff15",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: 6,
            background: "transparent",
            border: "none",
            borderTopWidth: 1,
            borderTopStyle: "solid",
            borderTopColor: "#ffffff15",
            cursor: "pointer",
            color: "#e8e1d180",
            width: "100%",
          }}
        >
          <span
            style={{
              width: 28,
              height: 28,
              background: "var(--banana)",
              color: "var(--ink)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 10,
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              border: "1px solid var(--ink)",
            }}
          >
            {initials}
          </span>
        </button>
      )}
    </div>
  );
}
