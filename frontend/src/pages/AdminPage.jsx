import { useState } from "react";
import Icon from "../components/Icon.jsx";
import { StatusDot } from "./admin/atoms.jsx";
import OverviewTab from "./admin/OverviewTab.jsx";
import UsersTab from "./admin/UsersTab.jsx";
import TiersTab from "./admin/TiersTab.jsx";
import ProvidersTab from "./admin/ProvidersTab.jsx";
import ConfigTab from "./admin/ConfigTab.jsx";
import CleanupTab from "./admin/CleanupTab.jsx";
import AnnouncementsTab from "./admin/AnnouncementsTab.jsx";
import AuditTab from "./admin/AuditTab.jsx";

const TABS = [
  { id: "overview", label: "Overview", Component: OverviewTab },
  { id: "users", label: "Users", count: 142, Component: UsersTab },
  { id: "tiers", label: "Tiers", Component: TiersTab },
  { id: "providers", label: "Providers", count: 4, Component: ProvidersTab },
  { id: "config", label: "Config", Component: ConfigTab },
  { id: "cleanup", label: "Cleanup", Component: CleanupTab },
  { id: "announcements", label: "Announcements", count: 2, Component: AnnouncementsTab },
  { id: "audit", label: "Audit", Component: AuditTab },
];

function AdminTab({ id, label, count, active, onClick }) {
  return (
    <button
      type="button"
      onClick={() => onClick(id)}
      data-admin-tab={id}
      data-active={active ? "true" : "false"}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "10px 14px",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        borderBottom: active ? "2px solid var(--ink)" : "2px solid transparent",
        fontFamily: "var(--font-sans)",
        fontSize: 13,
        fontWeight: active ? 700 : 500,
        color: active ? "var(--ink)" : "var(--ink-3)",
        letterSpacing: "-0.005em",
        marginBottom: -1,
        whiteSpace: "nowrap",
      }}
    >
      {label}
      {count != null && (
        <span
          className="mono"
          style={{
            fontSize: 10,
            padding: "1px 6px",
            background: active ? "var(--ink)" : "var(--paper-3)",
            color: active ? "var(--banana)" : "var(--ink-3)",
          }}
        >
          {count}
        </span>
      )}
    </button>
  );
}

export default function AdminPage({ initialTab = "overview" }) {
  const [tab, setTab] = useState(initialTab);
  const Active = TABS.find((t) => t.id === tab)?.Component || OverviewTab;
  // Only the Config tab carries an emergency banner in the design; reproducing
  // the same trigger so the banner shows up when an admin lands on Config.
  const alert = tab === "config" ? "force_captcha_global is ON" : null;

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        background: "var(--paper)",
        overflow: "hidden",
      }}
    >
      <div style={{ padding: "32px 40px 0", borderBottom: "1px solid var(--ink)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 24,
          }}
        >
          <div>
            <div
              className="mono caps"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                letterSpacing: "0.18em",
              }}
            >
              ADMIN · /api/admin
            </div>
            <h1
              className="display"
              style={{
                fontSize: 56,
                fontWeight: 900,
                letterSpacing: "-0.04em",
                margin: "16px 0 4px",
                lineHeight: 1,
              }}
            >
              Control{" "}
              <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
                room.
              </span>
            </h1>
            <div
              style={{
                fontSize: 14,
                color: "var(--ink-3)",
                marginTop: 8,
                fontStyle: "italic",
                fontFamily: "var(--font-display)",
              }}
            >
              users · tiers · providers · scheduler · cleanup · announcements
            </div>
          </div>

          <div style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
            <div
              style={{
                padding: "10px 14px",
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                minWidth: 180,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
              }}
            >
              <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
                SCHEDULER
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginTop: 4,
                }}
              >
                <StatusDot tone="ok" label="HEALTHY" />
              </div>
              <div
                className="mono"
                style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}
              >
                17/32 workers · 49 queued
              </div>
            </div>
            <button
              type="button"
              className="btn shadowed"
              style={{ height: "auto", alignSelf: "stretch", padding: "0 16px" }}
            >
              <Icon name="refresh" size={13} />
              Reload config
            </button>
          </div>
        </div>

        {alert && (
          <div
            style={{
              marginTop: 20,
              padding: "10px 14px",
              background: "var(--bad)",
              color: "var(--paper)",
              display: "flex",
              alignItems: "center",
              gap: 10,
              border: "1px solid var(--ink)",
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                background: "var(--banana)",
                borderRadius: "50%",
              }}
            />
            <span
              className="mono caps"
              style={{ fontSize: 11, letterSpacing: "0.12em", fontWeight: 700 }}
            >
              EMERGENCY · {alert}
            </span>
            <span style={{ marginLeft: "auto", fontSize: 12, opacity: 0.85 }}>
              set in Config / Emergency switches · admins bypass
            </span>
          </div>
        )}

        <div style={{ display: "flex", gap: 4, marginTop: 22, overflowX: "auto" }}>
          {TABS.map((t) => (
            <AdminTab
              key={t.id}
              id={t.id}
              label={t.label}
              count={t.count}
              active={tab === t.id}
              onClick={setTab}
            />
          ))}
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "28px 40px 56px" }}>
        <Active />
      </div>
    </div>
  );
}
