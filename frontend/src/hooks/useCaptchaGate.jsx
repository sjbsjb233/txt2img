// Shared captcha state machine for the three write-task entrypoints
// (Create, MaskEdit, Batch).
//
// Encapsulates:
//   - `precheckThenRun(builder)` — initial entry. Runs a precheck; if
//     the backend says captcha is required, opens the Turnstile modal,
//     waits for a token, then calls `builder(token)`. Returns whatever
//     `builder` returns.
//   - `acquireMidFlight()` — used inside fan-out worker pools when a
//     submission throws CAPTCHA_REQUIRED mid-flight. Opens the modal
//     once (latched against concurrent callers via `inFlightRef`),
//     resolves with the token. Rejects with CAPTCHA_CANCELLED if the
//     user closes the modal. Concurrent callers receive `null` so they
//     can spin in a pause-loop instead of opening their own modal.
//   - `Modal` — the React component the caller renders. Wires open
//     state, site key, and the resolve/reject bridges automatically.
//
// The hook is intentionally framework-light: it does not own the
// task queue or the worker loop — callers do, because the queue
// semantics differ between Create's fan-out and Batch's fan-out (the
// former re-enqueues; the latter could also re-enqueue but historically
// counts failures as "partial"). The hook only owns the bridge between
// "a 412 happened somewhere" and "a token comes back from a modal".

import { useCallback, useRef, useState } from "react";
import TurnstileModal from "../components/TurnstileModal.jsx";
import { precheck as precheckJob } from "../api/jobs.js";

export function useCaptchaGate({ modelId }) {
  const [showModal, setShowModal] = useState(false);
  const [siteKey, setSiteKey] = useState(null);
  // Promise resolver populated when the modal is opened on behalf of a
  // caller; resolved with the token by `onSuccess`, rejected by
  // `onClose`. A non-null value here means "a caller is waiting".
  const resolverRef = useRef(null);
  // Latch: only the first concurrent caller mounts the modal; others
  // back off and let it complete.
  const inFlightRef = useRef(false);

  const openModalAndWait = useCallback((nextSiteKey) => {
    if (nextSiteKey) setSiteKey(nextSiteKey);
    return new Promise((resolve, reject) => {
      resolverRef.current = { resolve, reject };
      setShowModal(true);
    });
  }, []);

  // Initial-entry path: ``builder(token)`` is called once we know
  // whether a token is needed. If the precheck says no captcha, builder
  // is called with ``null`` immediately.
  const precheckThenRun = useCallback(
    async (builder) => {
      let needs = false;
      let resolvedSiteKey = null;
      try {
        const check = await precheckJob({ model: modelId });
        if (check?.captcha_required) {
          needs = true;
          resolvedSiteKey = check.site_key || null;
        }
      } catch {
        // Precheck failures shouldn't block submission — the real
        // /api/jobs POST will surface a clearer error if any.
      }
      if (!needs) return await builder(null);
      const token = await openModalAndWait(resolvedSiteKey);
      return await builder(token);
    },
    [modelId, openModalAndWait]
  );

  // Mid-flight path: called from a worker-pool catch when ``err.code
  // === "CAPTCHA_REQUIRED"`` lands. Returns:
  //   - a token string when the user solves Turnstile
  //   - throws an error with code === "CAPTCHA_CANCELLED" if the user
  //     closes the modal
  //   - returns ``null`` when another concurrent caller is already
  //     handling the captcha (so this caller can spin in a pause-loop
  //     and pick up the token from a shared ref later)
  const acquireMidFlight = useCallback(async () => {
    if (inFlightRef.current) return null;
    inFlightRef.current = true;
    try {
      let resolvedSiteKey = null;
      try {
        const fresh = await precheckJob({ model: modelId });
        if (!fresh?.captcha_required) {
          // Race: gate cleared between the failed POST and now.
          // Caller should retry without a token.
          return null;
        }
        resolvedSiteKey = fresh.site_key || null;
      } catch {
        // Fall through with whatever site key the hook already cached.
      }
      return await openModalAndWait(resolvedSiteKey);
    } finally {
      inFlightRef.current = false;
    }
  }, [modelId, openModalAndWait]);

  const onSuccess = useCallback((token) => {
    setShowModal(false);
    const r = resolverRef.current;
    resolverRef.current = null;
    if (r) r.resolve(token);
  }, []);

  const onClose = useCallback(() => {
    setShowModal(false);
    const r = resolverRef.current;
    resolverRef.current = null;
    if (r) {
      const err = new Error("Captcha verification cancelled.");
      err.code = "CAPTCHA_CANCELLED";
      r.reject(err);
    }
  }, []);

  // Helper component the caller drops into JSX. Self-wired — the
  // caller doesn't need to pass any props.
  const Modal = useCallback(
    () => (
      <TurnstileModal
        open={showModal}
        siteKey={siteKey}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    ),
    [showModal, siteKey, onClose, onSuccess]
  );

  return {
    precheckThenRun,
    acquireMidFlight,
    Modal,
    // Exposed for callers that need to render their own modal (or for
    // tests). Normally use ``<gate.Modal />`` instead.
    showModal,
    siteKey,
  };
}
