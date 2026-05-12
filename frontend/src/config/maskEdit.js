// Configuration for the mask editor feature.
//
// Edit-with-mask supports two paths:
//   - "native": the provider has a real mask channel on its edit API
//     (e.g. gpt-image-2). We send the source as a single reference and
//     attach the alpha PNG mask in the ``mask`` multipart field.
//   - "fallback": the provider only does multimodal prompting (e.g.
//     Gemini 3). We send the source + a black/white mask image as the
//     second reference, and prepend a system template to the prompt
//     describing the convention.
//
// ``pickMaskMethod`` consumes the model's capabilities (the ones
// surfaced via /api/models) and returns "native" / "fallback" /
// "unsupported". The decision is made once at editor load — the user
// never sees this switch happen.

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

// Decide which mask-edit path a given model uses, based on its
// capabilities row (from /api/models). Returns one of:
//   - "native"      → real mask API; one ref + mask blob
//   - "fallback"    → bw-mask-in-references prompt template path
//   - "unsupported" → editor refuses to open
export function pickMaskMethod(capabilities) {
  if (!capabilities) return "unsupported";
  if (capabilities.supports_mask === true) return "native";
  const maxRefs = capabilities.max_reference_images;
  if (typeof maxRefs === "number" && maxRefs >= 2) return "fallback";
  return "unsupported";
}

// Cheap synchronous variant for the archive entry button — we don't
// have caps in hand there. Conservative: any image-y model is allowed
// to enter; the editor itself does the strict check on mount.
export function supportsMaskEdit(_model) {
  return true;
}

// ---------------------------------------------------------------------
// Fallback templates — copied verbatim from
// backend/app/domain/test_cases.py::_req_mask_inpaint(method="fallback")
// and ::_req_mask_outpaint(method="fallback"). Keeping them byte-identical
// matters: provider tests (M2/M4/M6/M8) baseline against these wordings,
// so a divergence here would silently shift edit-with-mask quality.
//
// ``{user_prompt}`` is the only placeholder. If absent, the user prompt
// is appended at the very end (one blank line before).
// ---------------------------------------------------------------------

export const FALLBACK_TEMPLATE_INPAINT =
`I'm giving you two images. The first image is the scene to edit. The second image is a black-and-white mask that defines which region to modify: the white region marks the area to be changed; the black region must remain unchanged.

{user_prompt}

Keep everything in the black region exactly the same as in the first image.`;

export const FALLBACK_TEMPLATE_OUTPAINT =
`I'm giving you two images. The first image is the scene to edit. The second image is a black-and-white mask that defines which region to fill: the white region marks the area to be newly generated; the black region must remain unchanged.

{user_prompt}

Keep everything in the black region exactly the same as in the first image.`;

// Resolve a fallback template against the user's prompt. If the
// template carries a ``{user_prompt}`` placeholder we substitute in
// place, otherwise we append the user prompt at the bottom (mirroring
// what the prompt would look like in audit logs).
export function resolveFallbackTemplate(template, userPrompt) {
  const tpl = typeof template === "string" ? template : "";
  const user = typeof userPrompt === "string" ? userPrompt : "";
  if (tpl.includes("{user_prompt}")) {
    return tpl.replace(/\{user_prompt\}/g, user);
  }
  if (!tpl.trim()) return user;
  return `${tpl}\n\n${user}`;
}

// Strategy thresholds for the post-success compare view. Tunable from
// here so we don't hunt through the page when calibrating against new
// model behavior. Numbers are percentages of "preserve change":
//   - p < SAFE_PRESERVE_PCT → mask-only is visually identical, picked
//     for archive precision.
//   - SAFE..ALARM → model leaked a bit; mask-only is the safer pick.
//   - p >= ALARM_PRESERVE_PCT → mask-only would show a seam; full
//     replace is the better default.
export const SAFE_PRESERVE_PCT = 5;
export const ALARM_PRESERVE_PCT = 15;

export function pickPreservationStrategy(preserveChangePct) {
  if (preserveChangePct == null || Number.isNaN(preserveChangePct)) return "mask";
  if (preserveChangePct >= ALARM_PRESERVE_PCT) return "full";
  return "mask";
}
