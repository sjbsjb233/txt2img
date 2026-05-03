import { useMemo } from "react";

// Build a render plan from (uiSchema, capabilities). For each field,
// decide whether it's interactive at all and which individual options
// are reachable under the user's current tier × provider mix. The
// renderer uses this directly — it never re-derives from capabilities.
export function useFieldRenderPlan(uiSchema, capabilities) {
  return useMemo(() => buildPlan(uiSchema, capabilities), [uiSchema, capabilities]);
}

// Pure function exported for unit tests; the hook is just a memo wrapper.
export function buildPlan(uiSchema, capabilities) {
  const plan = (uiSchema || []).map((field) => {
    const cap = capabilities?.[field.k];
    let allowedOptions = null;
    let fieldDisabled = false;
    let disabledReason = null;

    if (
      field.control === "chip-grid" ||
      field.control === "chip-row" ||
      field.control === "select"
    ) {
      if (!Array.isArray(cap) || cap.length === 0) {
        fieldDisabled = true;
        disabledReason = "Not available on your current tier.";
        allowedOptions = new Set();
      } else {
        allowedOptions = new Set(cap);
      }
    } else if (field.control === "number") {
      const max = typeof cap === "number" ? cap : null;
      const lowerBound = field.min ?? 1;
      if (max == null || max <= lowerBound) {
        if (max != null && max < lowerBound) {
          fieldDisabled = true;
          disabledReason = "Not adjustable for this model.";
        } else if (
          max != null &&
          max === lowerBound &&
          (field.presets?.length ?? 0) <= 1
        ) {
          // Locked to a single value (e.g. Gemini n=1) — still
          // render so the layout is stable, but no presets to pick.
          fieldDisabled = false;
        }
      }
    } else if (field.control === "toggle") {
      if (cap !== true) {
        fieldDisabled = true;
        disabledReason =
          cap === false
            ? "Provider has not enabled this feature."
            : "Not available on your current tier.";
      }
    }

    return { field, allowedOptions, fieldDisabled, disabledReason };
  });

  const byOrder = (a, b) => (a.field.order ?? 100) - (b.field.order ?? 100);
  const primary = plan
    .filter((p) => (p.field.group ?? "primary") === "primary")
    .sort(byOrder);
  const advanced = plan.filter((p) => p.field.group === "advanced").sort(byOrder);
  return { primary, advanced };
}
