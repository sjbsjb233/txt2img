import Icon from "../../components/Icon.jsx";
import { Hair, StatusDot } from "./atoms.jsx";

const PROVIDERS = [
  {
    id: "bltcy",
    label: "BLTCY",
    adapter: "gemini_v1beta",
    base: "https://api.bltcy.ai/v1beta",
    apiKey: "sk-***FQBR",
    state: "healthy",
    balance: 3.42,
    init: 3.88,
    cost: 0.1,
    conc: 12,
    max_conc: 70,
    rpm: 600,
    sr: 98.6,
    p50: 8.2,
    calls: 142,
    models: [
      "gemini-3-pro-image-preview",
      "gemini-3.1-flash-image-preview",
      "gemini-2.5-flash-image",
    ],
    tiers: ["vip", "premium", "standard"],
  },
  {
    id: "openai_official",
    label: "OpenAI Official",
    adapter: "openai_v1",
    base: "https://api.openai.com/v1",
    apiKey: "sk-***A2Lp",
    state: "healthy",
    balance: 24.18,
    init: 30.0,
    cost: 0.16,
    conc: 4,
    max_conc: 20,
    rpm: 200,
    sr: 100,
    p50: 5.1,
    calls: 56,
    models: ["gpt-image-2", "gpt-image-1.5"],
    tiers: ["vip", "premium"],
  },
  {
    id: "azure_eu",
    label: "Azure OpenAI EU",
    adapter: "openai_v1",
    base: "https://eu.api.azure-openai.com/v1",
    apiKey: "sk-***Z9kx",
    state: "half_open",
    balance: 12.0,
    init: 12.0,
    cost: 0.18,
    conc: 0,
    max_conc: 16,
    rpm: 120,
    sr: null,
    p50: null,
    calls: 0,
    models: ["gpt-image-2"],
    tiers: ["vip"],
    cooldown: "00:42",
  },
  {
    id: "relay_jp",
    label: "Relay JP",
    adapter: "gemini_v1beta",
    base: "https://relay-jp.example/v1beta",
    apiKey: "sk-***Q1xv",
    state: "drained",
    balance: 0.12,
    init: 5.0,
    cost: 0.08,
    conc: 0,
    max_conc: 30,
    rpm: 300,
    sr: 94.0,
    p50: 11.4,
    calls: 0,
    models: ["gemini-3.1-flash-image-preview"],
    tiers: ["standard", "free"],
  },
];

const ADAPTERS = [
  {
    type: "openai_v1",
    name: "OpenAI Compatible v1",
    desc: "OpenAI 官方 /images/generations 端点风格",
    models: ["gpt-image-2", "gpt-image-1.5", "dall-e-3"],
    inUse: ["openai_official", "azure_eu"],
  },
  {
    type: "gemini_v1beta",
    name: "Gemini v1beta",
    desc: "Google Gemini 原生 :generateContent 协议",
    models: [
      "gemini-3-pro-image-preview",
      "gemini-3.1-flash-image-preview",
      "gemini-2.5-flash-image",
    ],
    inUse: ["bltcy", "relay_jp"],
  },
];

const ALL_TIERS = ["vip", "premium", "standard", "free"];

function ProviderCard({ p }) {
  const drained = p.state === "drained";
  return (
    <div
      style={{
        border: "1px solid var(--ink)",
        background: "#fffdf7",
        position: "relative",
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: drained ? "var(--paper-2)" : "var(--ink)",
          color: drained ? "var(--ink)" : "var(--paper)",
          borderBottom: "1px solid var(--ink)",
        }}
      >
        <div
          style={{
            width: 28,
            height: 28,
            background: "var(--banana)",
            color: "var(--ink)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid var(--ink)",
          }}
        >
          {p.id.slice(0, 2).toUpperCase()}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{p.label}</div>
          <div className="mono" style={{ fontSize: 10, opacity: 0.75 }}>
            {p.id} · {p.adapter}
          </div>
        </div>
        <div
          style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}
        >
          <StatusDot
            tone={
              p.state === "healthy"
                ? "ok"
                : p.state === "half_open"
                  ? "warn"
                  : p.state === "drained"
                    ? "muted"
                    : "bad"
            }
            label={p.state.toUpperCase()}
          />
          {p.cooldown && (
            <span className="mono" style={{ fontSize: 10, opacity: 0.85 }}>
              cd {p.cooldown}
            </span>
          )}
        </div>
      </div>

      <div
        style={{
          padding: "14px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div
          className="mono"
          style={{ fontSize: 11, color: "var(--ink-2)", wordBreak: "break-all" }}
        >
          {p.base} <span style={{ color: "var(--ink-3)" }}>·</span>{" "}
          <span style={{ color: "var(--ink-3)" }}>key</span> {p.apiKey}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 8,
          }}
        >
          {[
            {
              l: "BALANCE",
              v: `¥${p.balance.toFixed(2)}`,
              sub: `of ¥${p.init.toFixed(2)}`,
              danger: p.balance < 0.5,
            },
            { l: "COST/IMG", v: `¥${p.cost.toFixed(2)}` },
            { l: "CONC", v: `${p.conc}/${p.max_conc}` },
            { l: "RPM", v: `${p.rpm}` },
          ].map((m) => (
            <div
              key={m.l}
              style={{
                padding: "8px 10px",
                border: "1px solid var(--ink-4)",
                background: m.danger ? "#f3d6d0" : "var(--paper-2)",
              }}
            >
              <div
                className="mono caps"
                style={{
                  fontSize: 8,
                  color: "var(--ink-3)",
                  letterSpacing: "0.14em",
                }}
              >
                {m.l}
              </div>
              <div
                className="mono"
                style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: m.danger ? "var(--bad)" : "var(--ink)",
                  marginTop: 2,
                }}
              >
                {m.v}
              </div>
              {m.sub && (
                <div
                  className="mono"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  {m.sub}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          <span style={{ color: "var(--ink)" }}>5min:</span> {p.calls} calls
          {" · "}
          <span
            style={{
              color:
                p.sr === 100
                  ? "var(--ok)"
                  : p.sr != null && p.sr < 95
                    ? "var(--bad)"
                    : "var(--ink-2)",
              fontWeight: 700,
            }}
          >
            {p.sr != null ? `${p.sr}% ok` : "no traffic"}
          </span>
          {p.p50 != null && <> · p50 {p.p50}s</>}
        </div>

        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 6 }}
          >
            SUPPORTED MODELS
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {p.models.map((m) => (
              <span key={m} className="chip" style={{ fontSize: 10 }}>
                {m}
              </span>
            ))}
          </div>
        </div>

        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 6 }}
          >
            TIER ACCESS
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {ALL_TIERS.map((t) => (
              <span
                key={t}
                style={{
                  padding: "2px 7px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  fontWeight: 700,
                  border: "1px solid var(--ink)",
                  background: p.tiers.includes(t) ? "var(--banana)" : "transparent",
                  color: p.tiers.includes(t) ? "var(--ink)" : "var(--ink-4)",
                  letterSpacing: "0.08em",
                }}
              >
                {t.toUpperCase()}
              </span>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", paddingTop: 4 }}>
          <button className="btn sm">Edit</button>
          <button className="btn sm">Test ping</button>
          <button className="btn sm">Top-up ¥</button>
          {p.state !== "healthy" && p.state !== "drained" && (
            <button className="btn sm">Reset circuit</button>
          )}
          <button className="btn sm">Capabilities</button>
        </div>
      </div>
    </div>
  );
}

export default function ProvidersTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: 16,
        }}
      >
        <div>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            /api/admin/providers
          </div>
          <div
            className="display"
            style={{
              fontSize: 32,
              fontWeight: 800,
              letterSpacing: "-0.025em",
              marginTop: 6,
            }}
          >
            Upstream relay{" "}
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
              fleet.
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn">
            <Icon name="download" size={13} />
            Export config
          </button>
          <button className="btn primary shadowed">
            <Icon name="plus" size={13} />
            Add provider
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {PROVIDERS.map((p) => (
          <ProviderCard key={p.id} p={p} />
        ))}
      </div>

      <div>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            marginBottom: 10,
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            REGISTERED ADAPTERS · /api/admin/adapters
          </div>
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            read-only · auto-discovered from disk
          </span>
        </div>
        <Hair thick />
        <div
          style={{
            marginTop: 12,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
          }}
        >
          {ADAPTERS.map((a) => (
            <div
              key={a.type}
              style={{
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                padding: 14,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
                  {a.type}
                </div>
                <span className="chip">{a.inUse.length} provider(s)</span>
              </div>
              <div style={{ fontSize: 13, fontWeight: 600, marginTop: 4 }}>{a.name}</div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--ink-3)",
                  marginTop: 4,
                  fontFamily: "var(--font-display)",
                  fontStyle: "italic",
                }}
              >
                {a.desc}
              </div>
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 8 }}
              >
                supports: {a.models.join(", ")}
              </div>
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 4 }}
              >
                in use by: {a.inUse.join(", ")}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
