// Section 06 — Storage & privacy. IndexedDB usage chip + Clear button,
// cache retention picker, screenshot-mode privacy toggle.

import { useEffect, useState } from "react";
import * as archiveDB from "../../storage/archiveDB.js";
import * as archiveStore from "../../store/archive.js";
import * as preferencesStore from "../../store/preferences.js";
import { useAuth } from "../../store/auth.js";
import {
  Chip,
  Icon,
  SectionCard,
  SectionHead,
  Segmented,
  SettingRow,
  Toggle,
} from "./components.jsx";

const RETENTION_OPTIONS = [
  { value: "keep_all", label: "Keep all" },
  { value: "last_30d", label: "Last 30d" },
  { value: "manual", label: "Manual only" },
];

function formatBytes(bytes) {
  if (bytes == null || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export default function SectionStorage({ prefs, local }) {
  const p = prefs.privacy;
  const { user } = useAuth();
  const [usage, setUsage] = useState({ usage: null, quota: null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refreshUsage = async () => {
    if (
      typeof navigator === "undefined" ||
      !navigator.storage ||
      typeof navigator.storage.estimate !== "function"
    ) {
      return;
    }
    try {
      const est = await navigator.storage.estimate();
      setUsage({ usage: est.usage, quota: est.quota });
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    void refreshUsage();
    const id = setInterval(refreshUsage, 5000);
    return () => clearInterval(id);
  }, []);

  const onResync = async () => {
    if (!user?.id) return;
    setError(null);
    setBusy(true);
    try {
      await archiveStore.reset();
    } catch (err) {
      setError(err?.message || "Re-sync failed.");
    } finally {
      setBusy(false);
      await refreshUsage();
    }
  };

  const onClear = async () => {
    if (!user?.id) return;
    if (
      !window.confirm(
        "Clear the local archive cache for this device? Images will be re-fetched on demand."
      )
    ) {
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await archiveDB.clear(user.id);
      await archiveStore.reset();
    } catch (err) {
      setError(err?.message || "Could not clear the local cache.");
    } finally {
      setBusy(false);
      await refreshUsage();
    }
  };

  return (
    <SectionCard>
      <SectionHead
        number="06"
        title="Storage & privacy"
        right={
          <Chip>
            IndexedDB · {usage.usage != null ? formatBytes(usage.usage) : "—"}
          </Chip>
        }
      />

      <SettingRow
        label="Local archive cache"
        hint={
          usage.usage != null && usage.quota != null
            ? `${formatBytes(usage.usage)} used · ${formatBytes(
                Math.max(0, usage.quota - usage.usage)
              )} free`
            : "Images and prompts cached for offline browsing of past jobs."
        }
      >
        <button
          className="btn sm"
          onClick={onResync}
          disabled={busy}
          title="Re-fetch the latest archive entries from the server."
        >
          <Icon name="refresh" size={11} /> Re-sync
        </button>
        <button
          className="btn sm"
          onClick={onClear}
          disabled={busy}
          title="Empty the local IndexedDB cache."
        >
          <Icon name="close" size={11} /> Clear
        </button>
      </SettingRow>

      {error && (
        <div
          style={{
            padding: "0 20px 10px",
            fontSize: 11,
            color: "var(--bad)",
          }}
        >
          {error}
        </div>
      )}

      <SettingRow
        label="Cache retention"
        hint="Older entries are evicted in the background when the page is idle."
        help="Manual only disables automatic eviction entirely; you'll need to use Clear above to recover space."
      >
        <Segmented
          value={local.cache_retention}
          options={RETENTION_OPTIONS}
          onChange={(v) => preferencesStore.setLocal("cache_retention", v)}
        />
      </SettingRow>

      <SettingRow
        label="Hide prompts in screenshot mode"
        hint="Replaces prompt text with a placeholder in the Archive grid."
        help="Useful for sharing screenshots with collaborators without leaking the prompt copy."
      >
        <Toggle
          checked={p.hide_prompts_in_screenshot_mode}
          onChange={(v) =>
            preferencesStore.setLocalDraft(
              "privacy",
              "hide_prompts_in_screenshot_mode",
              v
            )
          }
          aria-label="Hide prompts"
        />
      </SettingRow>
    </SectionCard>
  );
}
