// Section 05 — Language & region. Only English is wired up for the
// frontend strings right now (full i18n is a separate task), but we
// still persist the user's language/timezone/date-format preferences
// so the columns are ready when the translation bundles land.

import { useMemo } from "react";
import * as preferencesStore from "../../store/preferences.js";
import {
  Chip,
  SectionCard,
  SectionHead,
  SettingRow,
} from "./components.jsx";

// Allowed languages match the backend whitelist; all but English are
// placeholders until /i18n bundles ship.
const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "zh-CN", label: "简体中文 (preview)" },
  { value: "ja", label: "日本語 (preview)" },
  { value: "ko", label: "한국어 (preview)" },
];

const DATE_OPTIONS = [
  { value: "iso", label: "2026-05-02 (ISO)" },
  { value: "long", label: "02 May 2026" },
  { value: "us", label: "05/02/2026" },
];

function listTimezones() {
  if (typeof Intl === "undefined" || typeof Intl.supportedValuesOf !== "function") {
    return [
      "UTC",
      "Asia/Shanghai",
      "Asia/Tokyo",
      "Asia/Singapore",
      "Europe/London",
      "Europe/Berlin",
      "America/New_York",
      "America/Los_Angeles",
    ];
  }
  return Intl.supportedValuesOf("timeZone");
}

export default function SectionLocale({ prefs }) {
  const l = prefs.locale;
  const timezones = useMemo(() => listTimezones(), []);

  const setL = (key, value) =>
    preferencesStore.setLocalDraft("locale", key, value);

  return (
    <SectionCard>
      <SectionHead
        number="05"
        title="Language & region"
        right={<Chip>UTC offset auto</Chip>}
      />

      <SettingRow
        label="Interface language"
        hint="Only English is fully translated today; other languages will land progressively."
        help="Your selection is saved and will take effect once the matching translation bundle is published."
      >
        <select
          className="set-select"
          value={l.language}
          onChange={(e) => setL("language", e.target.value)}
        >
          {LANGUAGE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </SettingRow>

      <SettingRow
        label="Timezone"
        hint="Used for relative timestamps and the daily archive window."
      >
        <select
          className="set-select"
          style={{ minWidth: 220 }}
          value={l.timezone}
          onChange={(e) => setL("timezone", e.target.value)}
        >
          {timezones.map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </select>
      </SettingRow>

      <SettingRow
        label="Date format"
        hint="Affects every absolute date shown across the app."
      >
        <select
          className="set-select"
          value={l.date_format}
          onChange={(e) => setL("date_format", e.target.value)}
        >
          {DATE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </SettingRow>

      <SettingRow
        label="Daily archive window"
        hint="Managed by the system; aligned with your timezone."
        help="Older archive entries are organised into daily buckets at midnight in this timezone. The window is fixed by the system; it is not configurable per user."
      >
        <select className="set-select" disabled value="midnight">
          <option value="midnight">Daily at 00:00 (system)</option>
        </select>
      </SettingRow>
    </SectionCard>
  );
}
