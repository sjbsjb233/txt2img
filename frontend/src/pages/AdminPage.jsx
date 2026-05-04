import { useEffect, useState } from "react";
import Icon from "../components/Icon.jsx";
import { StatusDot } from "./admin/atoms.jsx";
import OverviewTab from "./admin/OverviewTab.jsx";
import UsersTab from "./admin/UsersTab.jsx";
import TiersTab from "./admin/TiersTab.jsx";
import ProvidersTab from "./admin/ProvidersTab.jsx";
import AdaptersTab from "./admin/AdaptersTab.jsx";
import ConfigTab from "./admin/ConfigTab.jsx";
import CleanupTab from "./admin/CleanupTab.jsx";
import AnnouncementsTab from "./admin/AnnouncementsTab.jsx";
import ApprovalsTab from "./admin/ApprovalsTab.jsx";
import AuditTab from "./admin/AuditTab.jsx";
import * as adminConfig from "../api/admin/config.js";
import * as adminApprovals from "../api/admin/approvals.js";
import * as adminMetrics from "../api/admin/metrics.js";
import * as adminAnnouncements from "../api/admin/announcements.js";

// `count` was hardcoded in the mock; we drop it on backend-wired tabs
// because the real numbers vary and the chip looked stale. The
// rendering pipeline still tolerates a `count` prop for tabs that
// haven't been wired yet.
const TABS = [
  { id: "overview", label: "Overview", Component: OverviewTab },
  { id: "users", label: "Users", Component: UsersTab },
  { id: "approvals", label: "Approvals", Component: ApprovalsTab },
  { id: "tiers", label: "Tiers", Component: TiersTab },
  { id: "providers", label: "Providers", Component: ProvidersTab },
  { id: "adapters", label: "Adapters", Component: AdaptersTab },
  { id: "config", label: "Config", Component: ConfigTab },
  { id: "cleanup", label: "Cleanup", Component: CleanupTab },
  { id: "announcements", label: "Announcements", Component: AnnouncementsTab },
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

// Map of emergency keys → human label for the top-of-page banner.
const EMERGENCY_LABELS = {
  "emergency.pause_generation": "pause_generation",
  "emergency.pause_image_access": "pause_image_access",
  "emergency.block_new_member_login": "block_new_member_login",
  "emergency.force_captcha_global": "force_captcha_global",
};

export default function AdminPage({ initialTab = "overview" }) {
  const [tab, setTab] = useState(initialTab);
  const [activeEmergencies, setActiveEmergencies] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState(0);
  const [schedulerView, setSchedulerView] = useState({
    healthy: null,
    workersInUse: null,
    workersMax: null,
    queued: null,
  });
  const [liveAnnouncements, setLiveAnnouncements] = useState(0);
  const Active = TABS.find((t) => t.id === tab)?.Component || OverviewTab;

  // Poll the overview snapshot for the header scheduler card. Same
  // 10s cadence as OverviewTab — duplicating the call is cheaper than
  // threading shared state through every tab.
  useEffect(() => {
    let alive = true;
    async function pull() {
      try {
        const data = await adminMetrics.getOverview();
        if (!alive) return;
        const queued = Object.values(data.queue_state || {}).reduce(
          (acc, lane) => acc + (lane?.queued || 0),
          0,
        );
        setSchedulerView({
          healthy: true,
          workersInUse: data.worker_pool?.in_use ?? 0,
          workersMax: data.worker_pool?.max ?? 0,
          queued,
        });
      } catch {
        if (alive) setSchedulerView((v) => ({ ...v, healthy: false }));
      }
    }
    pull();
    const t = setInterval(pull, 10_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  // Live announcement count drives the tab badge — we only flag the
  // tab when there are announcements *currently* visible to users.
  useEffect(() => {
    let alive = true;
    async function pull() {
      try {
        const res = await adminAnnouncements.listAnnouncements();
        if (!alive) return;
        const count = (res?.items || []).filter((a) => a.is_live).length;
        setLiveAnnouncements(count);
      } catch {
        /* leave stale; not load-bearing */
      }
    }
    pull();
    return () => {
      alive = false;
    };
  }, [tab]);

  // Pull the deletion-request pending count once per tab change so
  // the Approvals tab badge reflects the queue length without
  // requiring the admin to open it. Cheap query (id + status).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await adminApprovals.listDeletionRequests({
          status: "pending",
          limit: 1,
        });
        if (alive) setPendingApprovals(list?.total_pending || 0);
      } catch {
        /* keep stale count if the call fails */
      }
    })();
    return () => {
      alive = false;
    };
  }, [tab]);

  // Pull current config once so the emergency banner reflects live
  // state — the previous mock hard-coded the banner. Refreshes on
  // tab change so flipping a switch in Config and bouncing tabs
  // shows the result.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const cfg = await adminConfig.getConfig();
        if (!alive) return;
        const out = [];
        for (const k of Object.keys(EMERGENCY_LABELS)) {
          if (cfg[k]) out.push(EMERGENCY_LABELS[k]);
        }
        setActiveEmergencies(out);
      } catch {
        // Non-fatal — banner just stays empty if the call fails.
      }
    })();
    return () => {
      alive = false;
    };
  }, [tab]);

  const alert =
    activeEmergencies.length > 0
      ? `${activeEmergencies.join(" · ")} ON`
      : null;

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
                background: "var(--card)",
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
                {schedulerView.healthy === null ? (
                  <StatusDot tone="muted" label="…" />
                ) : schedulerView.healthy ? (
                  <StatusDot tone="ok" label="HEALTHY" />
                ) : (
                  <StatusDot tone="bad" label="UNREACHABLE" />
                )}
              </div>
              <div
                className="mono"
                style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}
              >
                {schedulerView.workersMax == null
                  ? "—"
                  : `${schedulerView.workersInUse}/${schedulerView.workersMax} workers · ${schedulerView.queued} queued`}
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
          {TABS.map((t) => {
            // Approvals + Announcements tabs get a live count badge.
            // Other tabs fall back to whatever static `count` was set.
            let dynamicCount = t.count;
            if (t.id === "approvals") dynamicCount = pendingApprovals || null;
            if (t.id === "announcements") dynamicCount = liveAnnouncements || null;
            return (
              <AdminTab
                key={t.id}
                id={t.id}
                label={t.label}
                count={dynamicCount}
                active={tab === t.id}
                onClick={setTab}
              />
            );
          })}
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "28px 40px 56px" }}>
        <Active />
      </div>
    </div>
  );
}
