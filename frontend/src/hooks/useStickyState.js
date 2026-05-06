import { useCallback, useEffect, useRef } from "react";
import {
  readSticky,
  writeSticky,
  migrateFromLegacyDraft,
} from "../storage/stickyStore.js";

const DEBOUNCE_MS = 600;

// Migration runs at most once per page-load per userId. Both readers
// and writers care that the legacy v1 draft has been consumed before
// they touch sticky, so we run it synchronously on the first render
// (during the first sync read in ``hydrateOnce``).
const migratedFor = new Set();

function hydrateOnce(userId) {
  if (!userId) return null;
  if (!migratedFor.has(userId)) {
    migratedFor.add(userId);
    try {
      migrateFromLegacyDraft(userId);
    } catch {
      // Migration must never block the page.
    }
  }
  return readSticky(userId) || null;
}

/**
 * Track + persist the user's "long-lived" Create page preferences:
 *   - last_model_id (the most recent model the user actually selected)
 *   - params_by_model (per-model parameter snapshots)
 *
 * This hook only reads the current ``selectedModelId`` and ``params``
 * the page is using; it does not own that state. Writes happen on a
 * debounced timer; ``flushSticky()`` lets the page force an immediate
 * write before risky operations (e.g. switching models).
 */
export function useStickyState({
  userId,
  selectedModelId,
  paramsForCurrentModel,
}) {
  // Synchronous hydrate so the consumer can read sticky on first render
  // (model resolution chain in CreatePage relies on this).
  const cacheRef = useRef(undefined);
  const hydratedForRef = useRef(null);
  if (cacheRef.current === undefined || hydratedForRef.current !== userId) {
    cacheRef.current = hydrateOnce(userId);
    hydratedForRef.current = userId;
  }

  const writeTimerRef = useRef(null);
  const liveRef = useRef({ selectedModelId, paramsForCurrentModel });
  liveRef.current = { selectedModelId, paramsForCurrentModel };

  const persistNow = useCallback(() => {
    if (!userId) return;
    const live = liveRef.current;
    const prev = cacheRef.current || {
      user_id: userId,
      last_model_id: null,
      params_by_model: {},
    };
    const params_by_model = { ...(prev.params_by_model || {}) };
    if (live.selectedModelId) {
      const params = live.paramsForCurrentModel || {};
      params_by_model[live.selectedModelId] = { ...params };
    }
    const payload = {
      user_id: userId,
      last_model_id: live.selectedModelId || prev.last_model_id || null,
      params_by_model,
    };
    writeSticky(payload);
    cacheRef.current = payload;
  }, [userId]);

  const flushSticky = useCallback(() => {
    if (writeTimerRef.current) {
      clearTimeout(writeTimerRef.current);
      writeTimerRef.current = null;
    }
    persistNow();
  }, [persistNow]);

  // Debounced write whenever model or params change. We don't care
  // about the prompt/refs/session part of state here — those are
  // owned by the Draft layer (useDraftAutosave).
  useEffect(() => {
    if (!userId || !selectedModelId) return undefined;
    if (writeTimerRef.current) clearTimeout(writeTimerRef.current);
    writeTimerRef.current = setTimeout(() => {
      writeTimerRef.current = null;
      persistNow();
    }, DEBOUNCE_MS);
    return () => {
      if (writeTimerRef.current) {
        clearTimeout(writeTimerRef.current);
        writeTimerRef.current = null;
      }
    };
  }, [userId, selectedModelId, paramsForCurrentModel, persistNow]);

  // Sync flush on tab unload — same defensive posture as draft.
  useEffect(() => {
    if (!userId) return undefined;
    const onBeforeUnload = () => {
      if (writeTimerRef.current) {
        clearTimeout(writeTimerRef.current);
        writeTimerRef.current = null;
        persistNow();
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [userId, persistNow]);

  useEffect(
    () => () => {
      if (writeTimerRef.current) clearTimeout(writeTimerRef.current);
    },
    []
  );

  const getParamsFor = useCallback((modelId) => {
    if (!modelId) return null;
    const cached = cacheRef.current;
    if (!cached || !cached.params_by_model) return null;
    const found = cached.params_by_model[modelId];
    return found ? { ...found } : null;
  }, []);

  return {
    hydratedSticky: cacheRef.current,
    flushSticky,
    getParamsFor,
  };
}
