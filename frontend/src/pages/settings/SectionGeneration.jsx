// Section 02 — Generation defaults. Default model / aspect ratio / batch
// size + auto-bind / auto-retry / remember-history toggles.
//
// Reads + writes the preferences store. The aspect ratio chips render
// a proportional ink rectangle so an unfamiliar ratio is still legible.

import { useEffect, useState } from "react";
import { getModels } from "../../api/models.js";
import * as preferencesStore from "../../store/preferences.js";
import {
  Chip,
  HelpTooltip,
  SectionCard,
  SectionHead,
  Segmented,
  SettingRow,
  Toggle,
} from "./components.jsx";

const RATIO_OPTIONS = ["1:1", "3:2", "16:9", "2:3", "9:16"];
const BATCH_OPTIONS = [
  { value: 1, label: "1" },
  { value: 2, label: "2" },
  { value: 4, label: "4" },
  { value: 8, label: "8" },
];

function ratioRect(ratio) {
  const [a, b] = String(ratio).split(":").map(Number);
  const max = 22;
  const r = (a || 1) / (b || 1);
  let w = max,
    h = max / r;
  if (h > max) {
    h = max;
    w = max * r;
  }
  return { w: Math.max(4, Math.round(w)), h: Math.max(4, Math.round(h)) };
}

export default function SectionGeneration({ prefs }) {
  const g = prefs.generation;
  const [models, setModels] = useState([]);

  useEffect(() => {
    let cancelled = false;
    getModels()
      .then((data) => {
        if (cancelled) return;
        setModels(data?.models || []);
      })
      .catch(() => {
        /* leave dropdown showing only "Last used" */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setG = (key, value) =>
    preferencesStore.setLocalDraft("generation", key, value);

  return (
    <SectionCard>
      <SectionHead
        number="02"
        title="Generation defaults"
        right={<Chip>Pre-fills the create page</Chip>}
      />

      <SettingRow
        label="Default model"
        hint="Loaded automatically when you open Create."
        help="Falls back to your most recent choice when the preferred model isn't available for a given request."
      >
        <select
          className="set-select"
          value={g.default_model_id || ""}
          onChange={(e) =>
            setG("default_model_id", e.target.value === "" ? null : e.target.value)
          }
        >
          <option value="">Last used</option>
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.label || m.model_id}
            </option>
          ))}
        </select>
      </SettingRow>

      <SettingRow
        label="Default aspect ratio"
        hint="Used when a model supports the choice; otherwise the model's own default wins."
      >
        <div style={{ display: "flex", gap: 6 }}>
          {RATIO_OPTIONS.map((r) => {
            const { w, h } = ratioRect(r);
            const on = g.default_aspect_ratio === r;
            return (
              <button
                key={r}
                type="button"
                className="ratio-chip"
                data-on={on ? "true" : "false"}
                onClick={() => setG("default_aspect_ratio", r)}
                title={r}
                aria-label={`Aspect ratio ${r}`}
              >
                <span style={{ width: w, height: h }} />
              </button>
            );
          })}
        </div>
      </SettingRow>

      <SettingRow
        label="Default batch size"
        hint="How many images Generate produces in a single submit."
      >
        <Segmented
          value={g.default_batch_size}
          options={BATCH_OPTIONS}
          onChange={(v) => setG("default_batch_size", v)}
        />
      </SettingRow>

      <SettingRow
        label="Auto-bind new jobs to the active session"
        hint="Otherwise prompts ask each time on Generate."
        help="When on, hitting Generate while a session is open puts the job inside that session immediately, no extra clicks."
      >
        <Toggle
          checked={g.auto_bind_session}
          onChange={(v) => setG("auto_bind_session", v)}
          aria-label="Auto-bind sessions"
        />
      </SettingRow>

      <SettingRow
        label="Auto-retry on transient provider errors"
        hint="Up to 3 retries via the scheduler's fallback chain."
      >
        <Toggle
          checked={g.auto_retry}
          onChange={(v) => setG("auto_retry", v)}
          aria-label="Auto-retry"
        />
      </SettingRow>

      <SettingRow
        label="Remember prompt history"
        hint="Last 50 prompts available in the Create page autocomplete."
      >
        <Toggle
          checked={g.remember_prompt_history}
          onChange={(v) => setG("remember_prompt_history", v)}
          aria-label="Remember prompt history"
        />
      </SettingRow>
    </SectionCard>
  );
}
