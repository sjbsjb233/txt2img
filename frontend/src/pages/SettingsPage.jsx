// /settings — user-facing preferences page.
//
// One column, nine sections. The top sticky save bar reflects whether
// any *synced* preference is dirty; profile edits and password changes
// have their own per-card save buttons because they hit different
// endpoints. Local-only preferences (reduce motion, interface scale,
// cache retention) save the moment they're toggled.

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import TopBar from "../components/TopBar.jsx";
import * as meApi from "../api/me.js";
import * as preferencesStore from "../store/preferences.js";
import SectionAppearance from "./settings/SectionAppearance.jsx";
import SectionDanger from "./settings/SectionDanger.jsx";
import SectionGeneration from "./settings/SectionGeneration.jsx";
import SectionLocale from "./settings/SectionLocale.jsx";
import SectionNotifications from "./settings/SectionNotifications.jsx";
import SectionProfile from "./settings/SectionProfile.jsx";
import SectionSecurity from "./settings/SectionSecurity.jsx";
import SectionShortcuts from "./settings/SectionShortcuts.jsx";
import SectionStorage from "./settings/SectionStorage.jsx";
import OnboardingTutorial from "../components/OnboardingTutorial.jsx";
import Icon from "../components/Icon.jsx";

export default function SettingsPage() {
  const navigate = useNavigate();
  const { prefs, local, dirty, loaded } = preferencesStore.usePreferences();
  const [me, setMe] = useState(null);
  const [meLoading, setMeLoading] = useState(true);
  const [saveError, setSaveError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);

  useEffect(() => {
    if (!loaded) {
      void preferencesStore.load();
    }
  }, [loaded]);

  useEffect(() => {
    let cancelled = false;
    setMeLoading(true);
    meApi
      .getMe()
      .then((data) => {
        if (cancelled) return;
        setMe(data);
      })
      .catch(() => {
        /* RequireAuth would have caught a 401; keep me=null so the
           sections gracefully degrade. */
      })
      .finally(() => {
        if (!cancelled) setMeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Warn the user if they try to navigate away with unsaved synced
  // preferences. Profile edits live in their own section component
  // and aren't tracked here — they can be lost with a route change,
  // but that surface area is small.
  useEffect(() => {
    if (!dirty) return undefined;
    const handler = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const onSave = async () => {
    setSaveError(null);
    setSaving(true);
    try {
      await preferencesStore.save();
    } catch (err) {
      setSaveError(err?.message || "Could not save preferences.");
    } finally {
      setSaving(false);
    }
  };

  const onReset = () => {
    if (!dirty) return;
    if (!window.confirm("Discard unsaved preference changes?")) return;
    preferencesStore.reset();
  };

  return (
    <div
      style={{
        flex: 1,
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      <TopBar
        crumb="Account"
        title="Settings"
        subtitle="Personal preferences and security for your account."
        right={
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
            synced to your account
          </span>
        }
      />

      <div
        className="set-savebar"
        data-dirty={dirty ? "true" : "false"}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
          <span
            className="caps"
            style={{ fontSize: 11, color: "var(--ink-3)" }}
          >
            Settings
          </span>
          <span
            className="display"
            style={{ fontSize: 22, fontWeight: 500, lineHeight: 1 }}
          >
            Preferences
          </span>
          {dirty && (
            <span
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-2)" }}
            >
              · unsaved changes
            </span>
          )}
          {saveError && (
            <span style={{ fontSize: 11, color: "var(--bad)" }}>
              {saveError}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn sm"
            onClick={onReset}
            disabled={!dirty || saving}
            title="Reset staged preference changes."
          >
            Reset
          </button>
          <button
            className="btn primary sm"
            onClick={onSave}
            disabled={!dirty || saving}
          >
            <Icon name="check" size={11} />
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "24px",
          display: "flex",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            width: "100%",
            maxWidth: 760,
            display: "flex",
            flexDirection: "column",
            gap: 28,
          }}
        >
          <SectionProfile me={me} onMeChanged={setMe} />
          <SectionGeneration prefs={prefs} />
          <SectionNotifications prefs={prefs} />
          <SectionAppearance prefs={prefs} local={local} />
          <SectionLocale prefs={prefs} />
          <SectionStorage prefs={prefs} local={local} />
          <SectionSecurity me={me} onMeChanged={setMe} />
          <SectionShortcuts onReplayTour={() => setTourOpen(true)} />
          <SectionDanger />
          <div style={{ height: 16 }} />
        </div>
      </div>

      <OnboardingTutorial
        open={tourOpen}
        onClose={() => setTourOpen(false)}
        onMinimise={() => setTourOpen(false)}
      />
    </div>
  );
}
