// Tiny no-deps JWT payload decoder. We only ever read non-secret claims
// (`sub`, `r`, `impersonator`, `exp`) for UI hints — the backend re-
// validates the signature on every request, so this never has to be
// trusted.

function base64UrlDecode(str) {
  // Translate base64url → standard base64 and pad.
  let s = str.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  try {
    return atob(s);
  } catch {
    return null;
  }
}

/**
 * Returns the decoded payload object, or `null` if the token is malformed
 * or the payload isn't valid JSON. Never throws.
 */
export function decodeJwtPayload(token) {
  if (typeof token !== "string" || !token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const decoded = base64UrlDecode(parts[1]);
  if (decoded === null) return null;
  try {
    return JSON.parse(decoded);
  } catch {
    return null;
  }
}

/** Convenience: returns true if the JWT carries an `impersonator` claim. */
export function isImpersonateToken(token) {
  const p = decodeJwtPayload(token);
  return !!(p && typeof p.impersonator === "string" && p.impersonator);
}

/** Returns the `exp` claim as ms since epoch, or `null`. */
export function expiryMs(token) {
  const p = decodeJwtPayload(token);
  if (!p || typeof p.exp !== "number") return null;
  return p.exp * 1000;
}
