// Configuration for the mask editor feature.
// See frontend design doc §3.1 — supportsMaskEdit() decides whether to
// render the "edit with mask" entry in the JobDrawer.

// Models we know support /v1/images/edits with a user mask.
// Once /api/models surfaces ``supports_mask`` in capabilities we can
// stop hard-coding here and read it from the model row.
const MASK_EDIT_MODELS = new Set([
  "gpt-image-2",
  "gpt-image-2-2026-04-21",
  // Allow generic gpt-image-2 family aliases.
]);

export function supportsMaskEdit(model) {
  if (!model) return false;
  if (MASK_EDIT_MODELS.has(model)) return true;
  // Fuzzy: tolerate dated variants like gpt-image-2-2026-XX-XX
  return /^gpt-image-2/i.test(model);
}

// Localised quick prompts shown as chips below the prompt textarea.
export const PROMPT_PRESETS = [
  { id: "remove", label: "remove this", insert: "remove this object" },
  { id: "replace", label: "replace with __", insert: "replace with " },
  { id: "color", label: "change color to __", insert: "change color to " },
  { id: "add", label: "add __", insert: "add " },
  { id: "make", label: "make it __", insert: "make it " },
  { id: "blur", label: "blur this", insert: "blur this region softly" },
];

// localStorage key — controls the "NEW" badge on the entry button.
export const NEW_BADGE_KEY = "mask_edit_used";

export function hasUsedMaskEdit() {
  try {
    return window.localStorage.getItem(NEW_BADGE_KEY) === "1";
  } catch {
    return true; // pessimistic when storage is unavailable
  }
}

export function markMaskEditUsed() {
  try {
    window.localStorage.setItem(NEW_BADGE_KEY, "1");
  } catch {
    // ignore — same fallback as hasUsedMaskEdit().
  }
}
