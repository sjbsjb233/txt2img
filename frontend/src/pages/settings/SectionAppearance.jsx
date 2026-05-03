// Section 04 — Appearance. Theme / density / sidebar default come from
// the synced preferences. Reduce-motion and Interface scale are local.

import * as preferencesStore from "../../store/preferences.js";
import {
  Chip,
  SectionCard,
  SectionHead,
  Segmented,
  SettingRow,
  Toggle,
} from "./components.jsx";

const THEME_OPTIONS = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];
const DENSITY_OPTIONS = [
  { value: "comfortable", label: "Comfortable" },
  { value: "compact", label: "Compact" },
];
const SIDEBAR_OPTIONS = [
  { value: "expanded", label: "Expanded" },
  { value: "rail", label: "Rail" },
];
const SCALE_OPTIONS = [
  { value: "A-", label: "A−" },
  { value: "A", label: "A" },
  { value: "A+", label: "A+" },
];

export default function SectionAppearance({ prefs, local }) {
  const a = prefs.appearance;

  const setA = (key, value) =>
    preferencesStore.setLocalDraft("appearance", key, value);
  const setLocal = (key, value) =>
    preferencesStore.setLocal(key, value);

  return (
    <SectionCard>
      <SectionHead
        number="04"
        title="Appearance"
        right={<Chip>Local to this browser</Chip>}
      />

      <SettingRow
        label="Theme"
        hint="Dark mode mirrors the brutalist palette with inverted ink and paper."
      >
        <Segmented
          value={a.theme}
          options={THEME_OPTIONS}
          onChange={(v) => setA("theme", v)}
        />
      </SettingRow>

      <SettingRow
        label="Density"
        hint="Compact tightens row padding across Archive and Create."
        help="Affects vertical rhythm only — type sizes stay the same."
      >
        <Segmented
          value={a.density}
          options={DENSITY_OPTIONS}
          onChange={(v) => setA("density", v)}
        />
      </SettingRow>

      <SettingRow
        label="Sidebar default"
        hint="Applies to new tabs; rail expands on hover."
      >
        <Segmented
          value={a.sidebar_default}
          options={SIDEBAR_OPTIONS}
          onChange={(v) => setA("sidebar_default", v)}
        />
      </SettingRow>

      <SettingRow
        label="Reduce motion"
        hint="Disables route fades and progress-bar animations."
        help="The system's own ‘prefers-reduced-motion’ setting still applies even when this is off."
      >
        <Toggle
          checked={Boolean(local.reduce_motion)}
          onChange={(v) => setLocal("reduce_motion", v)}
          aria-label="Reduce motion"
        />
      </SettingRow>

      <SettingRow
        label="Interface scale"
        hint="Scales the root font-size; affects the entire app uniformly."
      >
        <Segmented
          value={local.interface_scale}
          options={SCALE_OPTIONS}
          onChange={(v) => setLocal("interface_scale", v)}
        />
      </SettingRow>
    </SectionCard>
  );
}
