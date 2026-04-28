// Singleton loader for the Cloudflare Turnstile widget script.
//
// We intentionally use the explicit-render variant so we control when the
// widget mounts (lazy, only when a captcha is actually required). The script
// is small but loading it on every page paint would be wasteful and would
// also light up Cloudflare beacons before the user does anything.
//
// All callers go through `loadTurnstile()` which returns a Promise that
// resolves to `window.turnstile` once the SDK is ready. Concurrent calls
// share a single in-flight promise, so opening the modal twice does not
// inject the <script> tag twice.

const TURNSTILE_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

let _promise = null;

export function loadTurnstile() {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Turnstile only available in the browser"));
  }
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (_promise) return _promise;

  // Wrap every reject so a transient failure (network blip, ad-blocker
  // intercept, or window.turnstile missing on load) doesn't permanently
  // pin a rejected promise on the module — next caller starts fresh.
  _promise = new Promise((resolve, reject) => {
    const fail = (err) => {
      _promise = null;
      reject(err);
    };
    const onReady = () => {
      if (window.turnstile) resolve(window.turnstile);
      else fail(new Error("Turnstile script loaded but window.turnstile is missing"));
    };
    const existing = document.querySelector(`script[src^="${TURNSTILE_SRC}"]`);
    if (existing) {
      // Another consumer already injected it; just wait for load.
      if (window.turnstile) return resolve(window.turnstile);
      existing.addEventListener("load", onReady, { once: true });
      existing.addEventListener(
        "error",
        () => fail(new Error("Turnstile script failed to load")),
        { once: true },
      );
      return;
    }
    const tag = document.createElement("script");
    tag.src = TURNSTILE_SRC;
    tag.async = true;
    tag.defer = true;
    tag.addEventListener("load", onReady, { once: true });
    tag.addEventListener(
      "error",
      () => fail(new Error("Turnstile script failed to load")),
      { once: true },
    );
    document.head.appendChild(tag);
  });

  return _promise;
}
