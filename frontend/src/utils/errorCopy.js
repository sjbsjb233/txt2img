// Single source of truth for error-code → user-facing copy.
//
// All API errors arrive in the §17 envelope as `{detail: {code, message}}`.
// `apiFetch` runs every error through this lookup so the UI renders a
// frontend-controlled string regardless of what the backend felt like
// returning. If a code isn't in this map, the backend's message is used
// verbatim as the fallback.
//
// `null` means "this code should not be displayed to the user" — the call
// site is expected to handle it specially (e.g. CAPTCHA_REQUIRED triggers
// the Turnstile modal silently, no error banner).

export const ERROR_COPY = {
  // ----- auth / quota / rate / emergency (design doc §17, §7.1) -----
  HARD_QUOTA_EXCEEDED: "Daily limit reached. Try again tomorrow.",
  USER_BUSY: "You have too many jobs in flight. Wait for some to finish.",
  RATE_LIMITED: "Too fast. Slow down a bit.",
  BLOCKED_BY_EMERGENCY: "Service temporarily paused by admin.",
  ACCOUNT_DISABLED: "Your account is disabled. Contact admin.",
  CAPTCHA_REQUIRED: null, // handled silently by the caller
  CAPTCHA_INVALID: "Verification failed, please try again.",

  // ----- generic auth -----
  UNAUTHORIZED: "Invalid username or password.",
  FORBIDDEN: "You don't have permission to do that.",
  NOT_FOUND: "Resource not found.",
  BAD_REQUEST: "Request was malformed.",
  INVALID_PARAMETER: "One of the parameters isn't allowed for this model.",

  // ----- upstream / providers -----
  ALL_PROVIDERS_FAILED: "All providers failed. Try again in a moment.",
  NO_PROVIDER_AVAILABLE: "No provider is currently available for this model.",
  UPSTREAM_TIMEOUT: "The upstream provider timed out. Try again.",

  // ----- picker -----
  INVALID_PICKER_STATE: "Can't finalize yet — some images are still unjudged.",
  IMAGE_NOT_IN_SESSION: "This image isn't in the current session.",
  SESSION_HAS_NO_IMAGES: "Nothing to export — this session has no images.",
  SESSION_NOT_FINALIZED: "One or more sessions still need to be finalized.",
};

/**
 * Look up the user-facing copy for an error code.
 *
 * Returns:
 *   - the mapped string when the code is known and not silenced
 *   - `null` for explicitly silenced codes (caller handles them)
 *   - `undefined` when the code is unknown (caller falls back to the
 *     backend's `detail.message`)
 */
export function messageForCode(code) {
  if (!code) return undefined;
  if (Object.prototype.hasOwnProperty.call(ERROR_COPY, code)) {
    return ERROR_COPY[code];
  }
  return undefined;
}

/**
 * True when the code should be handled silently by the caller (no toast,
 * no error banner) — currently only CAPTCHA_REQUIRED.
 */
export function isSilentErrorCode(code) {
  return Object.prototype.hasOwnProperty.call(ERROR_COPY, code) && ERROR_COPY[code] === null;
}
