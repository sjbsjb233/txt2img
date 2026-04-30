import { useEffect, useMemo, useState } from "react";
import * as adminProviders from "../../api/admin/providers.js";

const ALL_TIERS = ["vip", "premium", "standard", "free"];

// Capability fields the form exposes. Keep aligned with the
// ProviderModelCapabilities pydantic schema. List-typed fields use
// chip toggles; numeric fields use number inputs; booleans use
// toggles. None of these are required — the backend treats null /
// missing as "no opinion at this layer".
const CAPABILITY_FIELDS = [
  { k: "size", kind: "list", options: ["1024x1024", "1536x1024", "1024x1536", "auto"] },
  {
    k: "aspect_ratio",
    kind: "list",
    options: [
      "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
      "9:16", "16:9", "21:9", "1:4", "4:1", "1:8", "8:1",
    ],
  },
  { k: "image_size", kind: "list", options: ["512", "1K", "2K", "4K"] },
  { k: "quality", kind: "list", options: ["low", "medium", "high", "auto"] },
  { k: "output_format", kind: "list", options: ["png", "jpeg", "webp"] },
  { k: "background", kind: "list", options: ["auto", "opaque"] },
  { k: "moderation", kind: "list", options: ["auto", "low"] },
  { k: "thinking_level", kind: "list", options: ["minimal", "high"] },
  { k: "n_max", kind: "int" },
  { k: "max_reference_images", kind: "int" },
  { k: "max_prompt_chars", kind: "int" },
  { k: "partial_images_max", kind: "int" },
  { k: "include_thoughts", kind: "bool" },
  { k: "google_search", kind: "bool" },
  { k: "image_search", kind: "bool" },
  { k: "stream", kind: "bool" },
  { k: "supports_transparent_bg", kind: "bool" },
  { k: "supports_mask", kind: "bool" },
];

function emptyModelEntry(modelId = "") {
  return {
    model_id: modelId,
    enabled: true,
    capabilities: {},
  };
}

function defaultForm() {
  return {
    provider_id: "",
    label: "",
    adapter_type: "",
    base_url: "",
    api_key: "",
    cost_per_image_cny: "0.10",
    initial_balance_cny: "5.0",
    enabled: true,
    note: "",
    max_concurrency: "10",
    rpm_limit: "600",
    supported_models: [],
    tier_access: ["vip", "premium"],
  };
}

function modelEntryFromServer(m) {
  return {
    model_id: m.model_id,
    enabled: !!m.enabled,
    capabilities: { ...(m.capabilities || {}) },
  };
}

function formFromProvider(p) {
  return {
    provider_id: p.id,
    label: p.label,
    adapter_type: p.adapter_type,
    base_url: p.base_url,
    api_key: "",
    cost_per_image_cny: String(p.cost_per_image_cny ?? "0"),
    initial_balance_cny: String(p.initial_balance_cny ?? "0"),
    enabled: !!p.enabled,
    note: p.note ?? "",
    max_concurrency: String(p.max_concurrency ?? 10),
    rpm_limit: String(p.rpm_limit ?? 600),
    supported_models: (p.supported_models || []).map(modelEntryFromServer),
    tier_access: [...(p.tier_access || [])],
  };
}

function buildCreatePayload(form) {
  const cost = Number(form.cost_per_image_cny);
  const balance = Number(form.initial_balance_cny);
  if (!Number.isFinite(cost) || cost < 0) throw new Error("cost_per_image_cny must be ≥ 0");
  if (!Number.isFinite(balance) || balance < 0)
    throw new Error("initial_balance_cny must be ≥ 0");
  return {
    provider_id: form.provider_id,
    label: form.label,
    adapter_type: form.adapter_type,
    base_url: form.base_url,
    api_key: form.api_key,
    cost_per_image_cny: cost,
    initial_balance_cny: balance,
    enabled: form.enabled,
    note: form.note || null,
    max_concurrency: Number(form.max_concurrency),
    rpm_limit: Number(form.rpm_limit),
    supported_models: form.supported_models.map((m) => ({
      model_id: m.model_id,
      enabled: m.enabled,
      capabilities: m.capabilities,
    })),
    tier_access: form.tier_access,
  };
}

function buildPatchPayload(form, original) {
  const out = {};
  if (form.label !== original.label) out.label = form.label;
  if (form.base_url !== original.base_url) out.base_url = form.base_url;
  if (form.api_key) out.api_key = form.api_key;
  const cost = Number(form.cost_per_image_cny);
  if (Number.isFinite(cost) && cost !== Number(original.cost_per_image_cny)) {
    out.cost_per_image_cny = cost;
  }
  if (form.enabled !== original.enabled) out.enabled = form.enabled;
  if ((form.note || null) !== (original.note ?? null)) {
    out.note = form.note || null;
  }
  const mc = Number(form.max_concurrency);
  if (Number.isFinite(mc) && mc !== Number(original.max_concurrency)) {
    out.max_concurrency = mc;
  }
  const rpm = Number(form.rpm_limit);
  if (Number.isFinite(rpm) && rpm !== Number(original.rpm_limit)) {
    out.rpm_limit = rpm;
  }
  return out;
}

function ListChip({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`chip ${active ? "solid" : ""}`}
      style={{ cursor: "pointer", marginRight: 4, marginBottom: 4 }}
    >
      {children}
    </button>
  );
}

function CapabilityEditor({ entry, onChange, allModels }) {
  const set = (k, v) => {
    const next = { ...entry.capabilities };
    if (v === undefined || v === null || v === "") delete next[k];
    else next[k] = v;
    onChange({ ...entry, capabilities: next });
  };
  const toggleList = (k, value) => {
    const cur = entry.capabilities[k] || [];
    if (cur.includes(value)) {
      const next = cur.filter((x) => x !== value);
      set(k, next.length ? next : undefined);
    } else {
      set(k, [...cur, value]);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: 12,
        border: "1px solid var(--ink-4)",
        background: "var(--paper-2)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <select
          className="inp"
          value={entry.model_id}
          onChange={(e) =>
            onChange({ ...entry, model_id: e.target.value })
          }
          style={{
            flex: 1,
            fontFamily: "var(--font-mono)",
            fontSize: 12,
          }}
        >
          <option value="">— pick a model —</option>
          {allModels.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <label
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--ink-2)",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <input
            type="checkbox"
            checked={entry.enabled}
            onChange={(e) =>
              onChange({ ...entry, enabled: e.target.checked })
            }
          />
          enabled
        </label>
      </div>

      {CAPABILITY_FIELDS.map((f) => {
        if (f.kind === "list") {
          const cur = entry.capabilities[f.k] || [];
          return (
            <div key={f.k}>
              <div
                className="mono caps"
                style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 4 }}
              >
                {f.k}
              </div>
              <div>
                {f.options.map((opt) => (
                  <ListChip
                    key={opt}
                    active={cur.includes(opt)}
                    onClick={() => toggleList(f.k, opt)}
                  >
                    {opt}
                  </ListChip>
                ))}
              </div>
            </div>
          );
        }
        if (f.kind === "int") {
          return (
            <div
              key={f.k}
              style={{ display: "flex", alignItems: "center", gap: 8 }}
            >
              <span
                className="mono caps"
                style={{ fontSize: 9, color: "var(--ink-3)", minWidth: 160 }}
              >
                {f.k}
              </span>
              <input
                type="number"
                className="inp"
                value={entry.capabilities[f.k] ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "") set(f.k, undefined);
                  else set(f.k, Number(v));
                }}
                style={{ width: 100, fontFamily: "var(--font-mono)" }}
              />
            </div>
          );
        }
        // bool
        return (
          <div
            key={f.k}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <span
              className="mono caps"
              style={{ fontSize: 9, color: "var(--ink-3)", minWidth: 160 }}
            >
              {f.k}
            </span>
            <select
              className="inp"
              value={
                entry.capabilities[f.k] === undefined
                  ? "unset"
                  : entry.capabilities[f.k]
                    ? "true"
                    : "false"
              }
              onChange={(e) => {
                const v = e.target.value;
                if (v === "unset") set(f.k, undefined);
                else set(f.k, v === "true");
              }}
              style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="unset">unset</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Modal for creating or editing a provider.
 *
 * @param props.mode "create" | "edit"
 * @param props.provider Server provider object when editing; null when creating.
 * @param props.adapters [{adapter_type, supported_models, ...}]
 * @param props.onClose () => void — close without saving.
 * @param props.onSaved (newOrUpdated) => void — close after a successful save.
 */
export default function ProviderEditorDialog({
  mode,
  provider,
  adapters,
  onClose,
  onSaved,
}) {
  const [form, setForm] = useState(() =>
    mode === "edit" && provider ? formFromProvider(provider) : defaultForm(),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (mode === "edit" && provider) setForm(formFromProvider(provider));
  }, [mode, provider]);

  const adapterEntry = useMemo(
    () => adapters.find((a) => a.adapter_type === form.adapter_type),
    [adapters, form.adapter_type],
  );
  const allModels = adapterEntry ? adapterEntry.supported_models : [];

  const setField = (k, v) => setForm((prev) => ({ ...prev, [k]: v }));

  const updateModel = (i, next) =>
    setForm((prev) => {
      const arr = [...prev.supported_models];
      arr[i] = next;
      return { ...prev, supported_models: arr };
    });

  const addModel = () =>
    setForm((prev) => ({
      ...prev,
      supported_models: [...prev.supported_models, emptyModelEntry()],
    }));

  const removeModel = (i) =>
    setForm((prev) => {
      const arr = [...prev.supported_models];
      arr.splice(i, 1);
      return { ...prev, supported_models: arr };
    });

  const toggleTier = (tier) =>
    setForm((prev) => {
      const has = prev.tier_access.includes(tier);
      return {
        ...prev,
        tier_access: has
          ? prev.tier_access.filter((t) => t !== tier)
          : [...prev.tier_access, tier],
      };
    });

  const onSubmit = async () => {
    setSaving(true);
    setError(null);
    try {
      if (mode === "create") {
        const payload = buildCreatePayload(form);
        const created = await adminProviders.createProvider(payload);
        onSaved(created);
      } else {
        const patch = buildPatchPayload(form, provider);
        if (Object.keys(patch).length > 0) {
          await adminProviders.patchProvider(provider.id, patch);
        }
        // models + tier-access patches go via dedicated endpoints —
        // we PATCH every existing model's capabilities (cheap, the
        // table is tiny) so the admin doesn't have to track which row
        // changed.
        for (const m of form.supported_models) {
          if (!m.model_id) continue;
          // Only the existing models can be patched in-place; v1
          // doesn't support adding a new model on edit (design doc
          // §13.4 explicitly defers this — admins delete + recreate).
          const original = (provider.supported_models || []).find(
            (x) => x.model_id === m.model_id,
          );
          if (!original) continue;
          await adminProviders.patchProviderModel(
            provider.id,
            m.model_id,
            { enabled: m.enabled, capabilities: m.capabilities },
          );
        }
        await adminProviders.patchTierAccess(
          provider.id,
          form.tier_access,
        );
        onSaved(provider);
      }
    } catch (err) {
      setError(err.message || "Failed to save provider.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
        padding: 24,
      }}
      onClick={() => !saving && onClose()}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(720px, 100%)",
          maxHeight: "calc(100vh - 80px)",
          background: "var(--paper)",
          border: "2px solid var(--ink)",
          boxShadow: "5px 5px 0 var(--ink)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "14px 18px",
            background: "var(--ink)",
            color: "var(--paper)",
          }}
        >
          <div className="display" style={{ fontSize: 22, fontWeight: 800 }}>
            {mode === "create" ? "New provider" : `Edit ${provider?.id}`}
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, opacity: 0.7, marginTop: 4 }}
          >
            {mode === "create"
              ? "POST /api/admin/providers"
              : `PATCH /api/admin/providers/${provider?.id}`}
          </div>
        </div>

        <div style={{ padding: 18, overflowY: "auto", flex: 1 }}>
          {error && (
            <div
              style={{
                padding: 10,
                marginBottom: 12,
                border: "1px solid var(--bad)",
                color: "var(--bad)",
                fontSize: 12,
              }}
            >
              {error}
            </div>
          )}

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 14,
            }}
          >
            <Field label="provider_id" mono>
              <input
                className="inp"
                disabled={mode === "edit"}
                value={form.provider_id}
                onChange={(e) => setField("provider_id", e.target.value.trim())}
                placeholder="bltcy"
                style={{ fontFamily: "var(--font-mono)" }}
              />
            </Field>
            <Field label="label">
              <input
                className="inp"
                value={form.label}
                onChange={(e) => setField("label", e.target.value)}
                placeholder="BLTCY"
              />
            </Field>
            <Field label="adapter_type">
              <select
                className="inp"
                value={form.adapter_type}
                disabled={mode === "edit"}
                onChange={(e) => setField("adapter_type", e.target.value)}
                style={{ fontFamily: "var(--font-mono)" }}
              >
                <option value="">— pick adapter —</option>
                {adapters.map((a) => (
                  <option key={a.adapter_type} value={a.adapter_type}>
                    {a.adapter_type} · {a.display_name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="base_url" mono>
              <input
                className="inp"
                value={form.base_url}
                onChange={(e) => setField("base_url", e.target.value.trim())}
                placeholder="https://api.example.com/v1"
                style={{ fontFamily: "var(--font-mono)" }}
              />
            </Field>
            <Field
              label={
                mode === "edit"
                  ? "api_key (leave blank to keep)"
                  : "api_key"
              }
              mono
            >
              <input
                className="inp"
                type="password"
                value={form.api_key}
                onChange={(e) => setField("api_key", e.target.value)}
                placeholder={
                  mode === "edit" ? "*** unchanged ***" : "sk-..."
                }
                style={{ fontFamily: "var(--font-mono)" }}
              />
            </Field>
            <Field label="cost_per_image_cny">
              <input
                className="inp"
                value={form.cost_per_image_cny}
                onChange={(e) =>
                  setField("cost_per_image_cny", e.target.value)
                }
                style={{ fontFamily: "var(--font-mono)" }}
              />
            </Field>
            <Field label="initial_balance_cny">
              <input
                className="inp"
                value={form.initial_balance_cny}
                disabled={mode === "edit"}
                onChange={(e) =>
                  setField("initial_balance_cny", e.target.value)
                }
                style={{ fontFamily: "var(--font-mono)" }}
              />
            </Field>
            <Field label="max_concurrency">
              <input
                className="inp"
                value={form.max_concurrency}
                onChange={(e) =>
                  setField("max_concurrency", e.target.value)
                }
                style={{ fontFamily: "var(--font-mono)" }}
              />
            </Field>
            <Field label="rpm_limit">
              <input
                className="inp"
                value={form.rpm_limit}
                onChange={(e) => setField("rpm_limit", e.target.value)}
                style={{ fontFamily: "var(--font-mono)" }}
              />
            </Field>
            <Field label="enabled">
              <label
                className="mono"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 12,
                }}
              >
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setField("enabled", e.target.checked)}
                />
                accept traffic
              </label>
            </Field>
          </div>

          <div style={{ marginTop: 14 }}>
            <Field label="note">
              <textarea
                className="inp"
                rows={2}
                value={form.note}
                onChange={(e) => setField("note", e.target.value)}
                style={{ width: "100%", fontFamily: "var(--font-display)" }}
              />
            </Field>
          </div>

          <div style={{ marginTop: 18 }}>
            <div
              className="mono caps"
              style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 6 }}
            >
              tier_access
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {ALL_TIERS.map((t) => (
                <button
                  type="button"
                  key={t}
                  className={`chip ${form.tier_access.includes(t) ? "solid" : ""}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => toggleTier(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 22 }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 8,
              }}
            >
              <div
                className="mono caps"
                style={{ fontSize: 10, color: "var(--ink-3)" }}
              >
                supported_models
              </div>
              <button
                type="button"
                className="btn sm"
                onClick={addModel}
                disabled={!form.adapter_type}
                title={
                  !form.adapter_type ? "pick an adapter first" : ""
                }
              >
                + add model
              </button>
            </div>
            {form.supported_models.length === 0 && (
              <div
                className="mono"
                style={{ fontSize: 11, color: "var(--ink-3)", fontStyle: "italic" }}
              >
                no models attached.
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {form.supported_models.map((m, i) => (
                <div key={i} style={{ position: "relative" }}>
                  <CapabilityEditor
                    entry={m}
                    onChange={(next) => updateModel(i, next)}
                    allModels={allModels}
                  />
                  <button
                    type="button"
                    className="btn sm"
                    onClick={() => removeModel(i)}
                    style={{ position: "absolute", top: 12, right: 12 }}
                  >
                    remove
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div
          style={{
            padding: "12px 18px",
            borderTop: "1px solid var(--ink)",
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            background: "var(--paper-2)",
          }}
        >
          <button
            type="button"
            className="btn"
            disabled={saving}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn primary shadowed"
            disabled={saving}
            onClick={onSubmit}
          >
            {saving ? "Saving…" : mode === "create" ? "Create provider" : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, mono, children }) {
  return (
    <div>
      <div
        className="mono caps"
        style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 4 }}
      >
        {label}
      </div>
      <div style={{ fontFamily: mono ? "var(--font-mono)" : "inherit" }}>
        {children}
      </div>
    </div>
  );
}
