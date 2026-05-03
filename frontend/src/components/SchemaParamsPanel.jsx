// Schema-driven Create-page parameter panel.
//
// Stand-alone for testability — every cell receives its plan entry
// (computed by useFieldRenderPlan) and renders the matching control
// flavour. The only React state inside is the <details>/<summary>
// expand/collapse for the Advanced section.

import { Fragment } from "react";
import { paramKey } from "../utils/paramKey.js";

// ---------------------------------------------------------------------------
// Aspect-ratio cell helpers — ChipGroup hands these off via renderOption
// for two specific schema fields whose chips need bespoke visuals.
// ---------------------------------------------------------------------------

function aspectBoxSize(ratio) {
  const parts = String(ratio || "1:1").split(":");
  const a = Math.max(1, Number(parts[0]) || 1);
  const b = Math.max(1, Number(parts[1]) || 1);
  // 36×32 fits the chip layout. Long ratios like 8:1 collapse below
  // the minimum so we floor at 4 px to keep them visible.
  const maxW = 36;
  const maxH = 32;
  const ratioVal = a / b;
  let w = maxW;
  let h = maxW / ratioVal;
  if (h > maxH) {
    h = maxH;
    w = maxH * ratioVal;
  }
  return { w: Math.max(4, Math.round(w)), h: Math.max(4, Math.round(h)) };
}

const IMAGE_SIZE_NOTES = {
  512: "preview",
  "1K": "balanced",
  "2K": "print",
  "4K": "max",
};

function aspectRatioRenderOption(opt, on) {
  const { w, h } = aspectBoxSize(opt);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
        color: on ? "var(--banana)" : "var(--ink)",
      }}
    >
      <div
        style={{
          width: w,
          height: h,
          background: on ? "var(--banana)" : "var(--paper-3)",
          border: "1px solid currentColor",
        }}
      />
      <div className="mono" style={{ fontSize: 10, fontWeight: 700 }}>
        {opt}
      </div>
    </div>
  );
}

function imageSizeRenderOption(opt, on) {
  return (
    <div>
      <div
        className="ticker"
        style={{
          fontSize: 20,
          fontWeight: 900,
          letterSpacing: "-0.03em",
          lineHeight: 1,
        }}
      >
        {opt}
      </div>
      <div
        className="mono"
        style={{ fontSize: 9, marginTop: 4, opacity: on ? 0.8 : 0.6 }}
      >
        {IMAGE_SIZE_NOTES[opt] || ""}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChipGroup — the work-horse list control. Renders a grid of buttons
// where each button can be individually disabled (capability says no)
// or the entire group can be disabled (no capability at all).
// ---------------------------------------------------------------------------

export function ChipGroup({
  label,
  hint,
  options,
  value,
  onChange,
  renderOption,
  smallTopMargin = false,
  isOptionAllowed,
  fieldDisabled = false,
  disabledReason = null,
  layout,
  showHairline = true,
  dataTestField,
  dataTestGroup,
}) {
  // Pick a column count by the longest label so chips fit the 360px right
  // panel without spilling. Calibrated against the ticker font (16px / 900).
  const longest = options.reduce(
    (acc, opt) => Math.max(acc, String(opt).length),
    0
  );
  let cols;
  if (layout === "row") {
    cols = Math.min(options.length || 1, 4);
  } else if (renderOption) {
    cols = Math.min(options.length || 1, layout === "grid" ? 3 : 4);
  } else if (longest >= 8) {
    cols = 2;
  } else if (longest >= 5) {
    cols = 3;
  } else {
    cols = Math.min(options.length || 1, 4);
  }
  return (
    <div
      data-test-field={dataTestField}
      data-test-group={dataTestGroup}
      data-test-disabled={fieldDisabled ? "true" : "false"}
      style={{
        marginTop: smallTopMargin ? 4 : 0,
        opacity: fieldDisabled ? 0.55 : 1,
      }}
      title={fieldDisabled ? disabledReason || undefined : undefined}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <div
          data-test-field-label
          className="mono caps"
          style={{
            fontSize: 10,
            color: fieldDisabled ? "var(--ink-4)" : "var(--ink-3)",
          }}
        >
          {label}
        </div>
        {hint ? (
          <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            {hint}
          </div>
        ) : null}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          gap: 6,
        }}
      >
        {options.map((opt) => {
          const on = value === opt;
          const allowed =
            !fieldDisabled &&
            (typeof isOptionAllowed === "function" ? isOptionAllowed(opt) : true);
          return (
            <button
              key={opt}
              data-test-option={String(opt)}
              data-test-active={on ? "true" : "false"}
              aria-disabled={!allowed}
              onClick={() => allowed && onChange(opt)}
              disabled={!allowed}
              title={
                fieldDisabled
                  ? disabledReason || undefined
                  : allowed
                  ? undefined
                  : "Not available on your current tier."
              }
              style={{
                padding: "10px 6px",
                background: on
                  ? "var(--ink)"
                  : allowed
                  ? "#fffdf7"
                  : "var(--paper-3)",
                border: "1px solid var(--ink)",
                color: on
                  ? "var(--banana)"
                  : allowed
                  ? "var(--ink)"
                  : "var(--ink-4)",
                cursor: allowed ? "pointer" : "not-allowed",
                textAlign: "center",
                minWidth: 0,
                overflow: "hidden",
                opacity: allowed ? 1 : 0.5,
              }}
            >
              {renderOption ? (
                renderOption(opt, on)
              ) : (
                <div
                  className="ticker"
                  style={{
                    fontSize: 16,
                    fontWeight: 900,
                    letterSpacing: "-0.03em",
                    lineHeight: 1.1,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {opt}
                </div>
              )}
            </button>
          );
        })}
      </div>
      {fieldDisabled && disabledReason ? (
        <div
          data-test-disabled-reason
          className="mono"
          style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 6 }}
        >
          {disabledReason}
        </div>
      ) : null}
      {showHairline ? <div className="hair" style={{ margin: "18px 0" }} /> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toggle — single boolean opt-in. Disabled when the merged caps don't
// include the feature.
// ---------------------------------------------------------------------------

export function Toggle({
  label,
  hint,
  value,
  onChange,
  fieldDisabled = false,
  disabledReason = null,
  dataTestField,
  dataTestGroup,
}) {
  return (
    <label
      data-test-field={dataTestField}
      data-test-group={dataTestGroup}
      data-test-disabled={fieldDisabled ? "true" : "false"}
      title={fieldDisabled ? disabledReason || undefined : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        background: fieldDisabled ? "var(--paper-3)" : "#fffdf7",
        border: "1px solid var(--ink)",
        padding: "8px 10px",
        cursor: fieldDisabled ? "not-allowed" : "pointer",
        opacity: fieldDisabled ? 0.55 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={value}
        disabled={fieldDisabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          data-test-field-label
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: fieldDisabled ? "var(--ink-4)" : undefined,
          }}
        >
          {label}
        </div>
        {hint ? (
          <div className="mono" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            {hint}
          </div>
        ) : null}
        {fieldDisabled && disabledReason ? (
          <div
            data-test-disabled-reason
            className="mono"
            style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 2 }}
          >
            {disabledReason}
          </div>
        ) : null}
      </div>
    </label>
  );
}

// ---------------------------------------------------------------------------
// NumberPresets — Output count specific. Carries the ticker (×N)
// visual the original mockup used; reserved for `n_max` today.
// ---------------------------------------------------------------------------

export function NumberPresets({
  label,
  hint,
  presets,
  max,
  value,
  onChange,
  fieldDisabled = false,
  disabledReason = null,
  dataTestField,
  dataTestGroup,
}) {
  const display = typeof value === "number" ? value : 1;
  const presetList = presets && presets.length ? presets : [1, 2, 4, 8];
  return (
    <div
      data-test-field={dataTestField}
      data-test-group={dataTestGroup}
      data-test-disabled={fieldDisabled ? "true" : "false"}
      title={fieldDisabled ? disabledReason || undefined : undefined}
      style={{ opacity: fieldDisabled ? 0.55 : 1 }}
    >
      <div
        data-test-field-label
        className="mono caps"
        style={{
          fontSize: 10,
          color: fieldDisabled ? "var(--ink-4)" : "var(--ink-3)",
          marginBottom: 8,
        }}
      >
        {label}
      </div>
      <div
        style={{
          border: "1px solid var(--ink)",
          padding: 12,
          background: fieldDisabled ? "var(--paper-3)" : "#fffdf7",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
          }}
        >
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
            {hint || ""}
          </span>
          <div
            data-test-output-count
            className="ticker"
            style={{ fontSize: 24, fontWeight: 900, letterSpacing: "-0.03em" }}
          >
            {display}
          </div>
        </div>
        <div style={{ display: "flex", gap: 4, marginTop: 8 }}>
          {presetList.map((n) => {
            const allowed =
              !fieldDisabled && (typeof max === "number" ? n <= max : true);
            const on = display === n;
            return (
              <button
                key={n}
                data-test-option={String(n)}
                data-test-active={on ? "true" : "false"}
                aria-disabled={!allowed}
                onClick={() => allowed && onChange(n)}
                disabled={!allowed}
                title={
                  fieldDisabled
                    ? disabledReason || undefined
                    : allowed
                    ? undefined
                    : `caps at ${max}`
                }
                style={{
                  flex: 1,
                  height: 30,
                  background: on
                    ? "var(--banana)"
                    : allowed
                    ? "transparent"
                    : "var(--paper-3)",
                  border: "1px solid var(--ink)",
                  cursor: allowed ? "pointer" : "not-allowed",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  fontWeight: 700,
                  color: allowed ? "var(--ink)" : "var(--ink-4)",
                  opacity: allowed ? 1 : 0.5,
                }}
              >
                {n}
              </button>
            );
          })}
        </div>
      </div>
      {fieldDisabled && disabledReason ? (
        <div
          data-test-disabled-reason
          className="mono"
          style={{ fontSize: 9, color: "var(--ink-4)", marginTop: 4 }}
        >
          {disabledReason}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FieldRenderer — single-field router. Picks the matching control
// flavour from plan.field.control and forwards data-test attributes.
// ---------------------------------------------------------------------------

export function FieldRenderer({ plan, value, onChange, capabilities }) {
  const { field, allowedOptions, fieldDisabled, disabledReason } = plan;
  const isAllowed = (opt) =>
    allowedOptions ? allowedOptions.has(opt) : true;
  const dataTestField = field.k;
  const dataTestGroup = field.group ?? "primary";

  if (
    field.control === "chip-grid" ||
    field.control === "chip-row" ||
    field.control === "select"
  ) {
    let renderOption;
    if (field.k === "aspect_ratio") renderOption = aspectRatioRenderOption;
    else if (field.k === "image_size") renderOption = imageSizeRenderOption;

    return (
      <ChipGroup
        label={field.label}
        hint={field.hint || null}
        options={field.options || []}
        value={value ?? null}
        onChange={onChange}
        renderOption={renderOption}
        layout={field.control === "chip-row" ? "row" : "grid"}
        isOptionAllowed={isAllowed}
        fieldDisabled={fieldDisabled}
        disabledReason={disabledReason}
        showHairline={false}
        dataTestField={dataTestField}
        dataTestGroup={dataTestGroup}
      />
    );
  }

  if (field.control === "number") {
    const cap = capabilities?.[field.k];
    const max = typeof cap === "number" ? cap : field.max ?? null;
    return (
      <NumberPresets
        label={field.label}
        hint={field.hint || null}
        presets={field.presets || [1, 2, 4, 8]}
        max={max}
        value={typeof value === "number" ? value : 1}
        onChange={onChange}
        fieldDisabled={fieldDisabled}
        disabledReason={disabledReason}
        dataTestField={dataTestField}
        dataTestGroup={dataTestGroup}
      />
    );
  }

  if (field.control === "toggle") {
    return (
      <Toggle
        label={field.label}
        hint={field.hint || null}
        value={!!value}
        onChange={onChange}
        fieldDisabled={fieldDisabled}
        disabledReason={disabledReason}
        dataTestField={dataTestField}
        dataTestGroup={dataTestGroup}
      />
    );
  }

  return null;
}

// ---------------------------------------------------------------------------
// SchemaParamsPanel — top-level panel layout: ordered primary fields
// with hairlines, then ◢ Advanced collapse holding the rest.
// ---------------------------------------------------------------------------

export function SchemaParamsPanel({ plan, params, setParam, capabilities }) {
  if (!plan.primary.length && !plan.advanced.length) {
    return (
      <div
        data-test-empty-panel
        className="mono"
        style={{ fontSize: 11, color: "var(--ink-3)", padding: 12 }}
      >
        This model has no configurable parameters.
      </div>
    );
  }
  return (
    <>
      {plan.primary.map((p, idx) => {
        const pk = paramKey(p.field);
        return (
          <Fragment key={p.field.k}>
            <FieldRenderer
              plan={p}
              value={params[pk]}
              onChange={(v) => setParam(pk, v)}
              capabilities={capabilities}
            />
            {idx < plan.primary.length - 1 ? (
              <div className="hair" style={{ margin: "18px 0" }} />
            ) : null}
          </Fragment>
        );
      })}
      {plan.advanced.length > 0 ? (
        <>
          <div className="hair" style={{ margin: "18px 0" }} />
          <details data-test-advanced>
            <summary
              className="mono caps"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                cursor: "pointer",
                outline: "none",
                userSelect: "none",
              }}
            >
              ◢ Advanced
            </summary>
            <div
              style={{
                marginTop: 12,
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              {plan.advanced.map((p) => {
                const pk = paramKey(p.field);
                return (
                  <FieldRenderer
                    key={p.field.k}
                    plan={p}
                    value={params[pk]}
                    onChange={(v) => setParam(pk, v)}
                    capabilities={capabilities}
                  />
                );
              })}
            </div>
          </details>
        </>
      ) : null}
    </>
  );
}
