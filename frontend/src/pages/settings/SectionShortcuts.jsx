// Section 08 — Shortcuts & help. Pure-static. Triggers the onboarding
// tutorial via localStorage flag.

import {
  Chip,
  Icon,
  SectionCard,
  SectionHead,
  SettingRow,
} from "./components.jsx";
import { useAuth } from "../../store/auth.js";

const APP_VERSION = import.meta.env?.VITE_APP_VERSION || "dev";

const SHORTCUTS = [
  { label: "Open Create", keys: ["G", "C"] },
  { label: "Open Archive", keys: ["G", "A"] },
  { label: "Quick search", keys: ["⌘", "K"] },
  { label: "Generate", keys: ["⌘", "↵"] },
  { label: "Toggle sidebar", keys: ["⌘", "\\"] },
  { label: "Focus prompt", keys: ["/"] },
];

function macModifier() {
  if (typeof navigator === "undefined") return "⌘";
  return /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";
}

function Kbd({ k }) {
  return <span className="kbd">{k}</span>;
}

export default function SectionShortcuts({ onReplayTour }) {
  const { user } = useAuth();
  const mod = macModifier();

  const onReplay = () => {
    if (user?.id) {
      try {
        localStorage.removeItem(`txt2img_tutorial_seen_${user.id}`);
      } catch {
        /* ignore */
      }
    }
    onReplayTour && onReplayTour();
  };

  const docsUrl = import.meta.env?.VITE_DOCS_URL;

  return (
    <SectionCard>
      <SectionHead
        number="08"
        title="Shortcuts & help"
      />

      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--rule)" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "10px 24px",
          }}
        >
          {SHORTCUTS.map((sc) => (
            <div
              key={sc.label}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
              }}
            >
              <span style={{ fontSize: 12, color: "var(--ink)" }}>
                {sc.label}
              </span>
              <span style={{ display: "inline-flex", gap: 4 }}>
                {sc.keys.map((k, i) => (
                  <Kbd key={i} k={k === "⌘" ? mod : k} />
                ))}
              </span>
            </div>
          ))}
        </div>
      </div>

      <SettingRow
        label="Replay onboarding tutorial"
        hint="Resets the per-user 'seen' flag and reopens the walkthrough."
      >
        <button className="btn sm" onClick={onReplay}>
          <Icon name="refresh" size={11} /> Start tour
        </button>
      </SettingRow>

      <SettingRow
        label="Documentation"
        hint="Prompt guide, model capabilities, troubleshooting."
      >
        {docsUrl ? (
          <a className="btn sm" href={docsUrl} target="_blank" rel="noreferrer">
            <Icon name="arrow" size={11} /> Open docs
          </a>
        ) : (
          <button className="btn sm" disabled title="Configure VITE_DOCS_URL to enable.">
            Open docs
          </button>
        )}
      </SettingRow>

      <div
        style={{
          padding: "10px 20px",
          fontSize: 11,
          color: "var(--ink-3)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span className="mono">Build {APP_VERSION}</span>
        <Chip>{mod} = primary modifier</Chip>
      </div>
    </SectionCard>
  );
}
