// Section 03 — Notifications. Browser permission state lives at
// ``Notification.permission`` so we read it live rather than caching.

import { useEffect, useState } from "react";
import * as preferencesStore from "../../store/preferences.js";
import {
  Chip,
  SectionCard,
  SectionHead,
  Segmented,
  SettingRow,
  Toggle,
} from "./components.jsx";

const ANN_OPTIONS = [
  { value: "all", label: "All" },
  { value: "important", label: "Important only" },
  { value: "none", label: "None" },
];

function permissionTone(p) {
  if (p === "granted") return "ok";
  if (p === "denied") return "bad";
  return "default";
}

function permissionLabel(p) {
  if (p === "granted") return "Granted";
  if (p === "denied") return "Denied";
  if (p === "unsupported") return "Unsupported";
  return "Not asked";
}

export default function SectionNotifications({ prefs }) {
  const n = prefs.notifications;
  const [permission, setPermission] = useState(() =>
    typeof Notification === "undefined"
      ? "unsupported"
      : Notification.permission
  );
  const [permError, setPermError] = useState(null);

  useEffect(() => {
    if (typeof Notification === "undefined") return undefined;
    const tick = () => setPermission(Notification.permission);
    // Browsers don't fire permission-change consistently; poll every
    // few seconds while the page is visible. Cheap, no network.
    const interval = setInterval(tick, 4000);
    return () => clearInterval(interval);
  }, []);

  const setN = (key, value) =>
    preferencesStore.setLocalDraft("notifications", key, value);

  const onBrowserToggle = async (on) => {
    setPermError(null);
    if (!on) {
      setN("browser_on_complete", false);
      return;
    }
    if (typeof Notification === "undefined") {
      setPermError("Browser notifications are not supported here.");
      return;
    }
    if (Notification.permission === "denied") {
      setPermError(
        "Permission was denied. Re-enable notifications for this site in your browser settings."
      );
      return;
    }
    if (Notification.permission !== "granted") {
      try {
        const result = await Notification.requestPermission();
        setPermission(result);
        if (result !== "granted") {
          setPermError("Permission was not granted.");
          return;
        }
      } catch {
        setPermError("Could not request notification permission.");
        return;
      }
    }
    setN("browser_on_complete", true);
  };

  return (
    <SectionCard>
      <SectionHead
        number="03"
        title="Notifications"
        right={
          <Chip tone={permissionTone(permission)}>
            Browser permission · {permissionLabel(permission)}
          </Chip>
        }
      />

      <SettingRow
        label="Browser notification when a job completes"
        hint="Fires through the existing SSE stream — no polling."
      >
        <Toggle
          checked={n.browser_on_complete}
          onChange={onBrowserToggle}
          disabled={permission === "unsupported"}
          aria-label="Browser notification on complete"
        />
      </SettingRow>

      {permError && (
        <div
          style={{
            padding: "0 20px 10px",
            fontSize: 11,
            color: "var(--bad)",
          }}
        >
          {permError}
        </div>
      )}

      <SettingRow
        label="Sound on completion"
        hint="A short chime; muted while another tab is active."
      >
        <Toggle
          checked={n.sound_on_complete}
          onChange={(v) => setN("sound_on_complete", v)}
          aria-label="Sound on complete"
        />
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={n.sound_volume}
          className="range-banana"
          onChange={(e) => setN("sound_volume", Number(e.target.value))}
          disabled={!n.sound_on_complete}
          aria-label="Sound volume"
        />
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--ink-3)",
            minWidth: 28,
            textAlign: "right",
          }}
        >
          {n.sound_volume}
        </span>
      </SettingRow>

      <SettingRow
        label="Desktop badge for queued jobs"
        hint="Shows a count on the Archive entry in the sidebar."
      >
        <Toggle
          checked={n.desktop_badge}
          onChange={(v) => setN("desktop_badge", v)}
          aria-label="Desktop badge"
        />
      </SettingRow>

      <SettingRow
        label="Show admin announcements"
        hint="Banner stack at the top of every page; you can still dismiss them."
        help="Critical announcements (outages, mandatory updates) are always shown regardless of this setting."
      >
        <Segmented
          value={n.announcements_level}
          options={ANN_OPTIONS}
          onChange={(v) => setN("announcements_level", v)}
        />
      </SettingRow>
    </SectionCard>
  );
}
