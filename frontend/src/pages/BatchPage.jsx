// Batch page (frontend doc v0.3).
//
// Two physically separate sections:
//   - Top: running batch list (server truth, SSE-driven).
//   - Bottom: editor (local-only draft).
//
// The page deliberately avoids cross-pollination — running cards never
// reflect editor state, and the editor never re-renders progress for a
// submitted batch. State boundary is the single Submit click which
// 1) registers the batch, 2) clears the editor, 3) fans Job posts out
// asynchronously, 4) finalises submission.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Icon from "../components/Icon.jsx";
import TopBar from "../components/TopBar.jsx";
import RunningBatchList from "../components/batch/RunningBatchList.jsx";
import BatchDetailDrawer from "../components/batch/BatchDetailDrawer.jsx";
import FixedBlock from "../components/batch/FixedBlock.jsx";
import SlotCard from "../components/batch/SlotCard.jsx";
import RightRail from "../components/batch/RightRail.jsx";
import ActionBar from "../components/batch/ActionBar.jsx";

import {
  cancelBatchQueued,
  createBatch,
  deleteBatch,
  finalizeBatch,
} from "../api/batches.js";
import { createJob } from "../api/jobs.js";
import { getModels } from "../api/models.js";
import * as batchStore from "../store/batch.js";
import { useAuth } from "../store/auth.js";
import {
  clearDraft,
  getDraft,
  putDraft,
} from "../storage/batchDraftDB.js";

// ---------------------------------------------------------------------------
// Constants / helpers
// ---------------------------------------------------------------------------

const MAX_SLOTS = 50;
const MAX_TOTAL_IMAGES = 400;

function nano(prefix) {
  // 10-char alphanumeric, lowercase + digits — matches backend regex.
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < 10; i += 1) {
    out += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return `${prefix}_${out}`;
}

function emptyDraft() {
  return {
    title: "Untitled batch",
    fixed: { prompt: "", refs: [], append_mode: "prepend" },
    slots: [],
    session_strategy: "per_slot_new",
    default_image_count: 4,
  };
}

function newSlot(stableIdx, defaults = {}) {
  return {
    slot_id: nano("slt"),
    stable_idx: stableIdx,
    rev: 0,
    title: defaults.title || `Slot ${stableIdx}`,
    prompt: defaults.prompt || "",
    refs: [],
    notes: "",
    image_count: defaults.image_count || 4,
    set_id: null,
    overrides: {},
    session_strategy: "inherit",
    session_target: null,
    model_override: null,
  };
}

function specFromDraft(draft, totalImages) {
  return {
    fixed_prompt_summary: (draft.fixed.prompt || "").slice(0, 200),
    fixed_ref_count: (draft.fixed.refs || []).length,
    session_strategy: draft.session_strategy,
    shared_session_id: null,
    slots: draft.slots.map((s) => ({
      stable_idx: s.stable_idx,
      title: (s.title || "").slice(0, 200),
      prompt_summary: (s.prompt || "").slice(0, 200),
      image_count: s.image_count,
      set_id: s.set_id || null,
      session_id: null,
    })),
  };
}

/**
 * Return a copy of ``draft`` with a freshly allocated ``set_id`` on every
 * slot. Called once before stage ① of submit so the value the spec ships
 * to ``POST /api/batches`` matches the value each ``POST /api/jobs`` sends
 * downstream — without that match the detail drawer's
 * ``_find_slot_for_set_id`` falls back to a synthetic ``(unbound)``
 * bucket and the per-slot 4/4 ✓ progression collapses.
 *
 * We allocate for ``image_count == 1`` slots too so two single-image
 * slots in the same batch can be told apart in the drawer (the backend's
 * fallback for ``set_id == null`` picks the first matching slot, which
 * was masking the second slot under the first one).
 */
function prepareDraftForSubmit(draft) {
  const slots = draft.slots.map((s) => ({
    ...s,
    set_id: s.set_id || nano("set"),
  }));
  return { ...draft, slots };
}

function buildJobTasks(draft) {
  // Flatten the slot list into one Job per image. ``draft`` here is the
  // prepared snapshot from :func:`prepareDraftForSubmit`, so every slot
  // already has an allocated ``set_id`` we just thread through.
  const tasks = [];
  for (const slot of draft.slots) {
    const setId = slot.set_id;
    for (let i = 0; i < slot.image_count; i += 1) {
      tasks.push({
        slot,
        set_id: setId,
        prompt:
          draft.fixed.append_mode === "prepend"
            ? `${draft.fixed.prompt}\n\n${slot.prompt}`.trim()
            : `${slot.prompt}\n\n${draft.fixed.prompt}`.trim(),
        client_request_id: `${slot.slot_id}_${slot.rev}_${i + 1}`,
        ordinal: i + 1,
        of: slot.image_count,
      });
    }
  }
  return tasks;
}

// Small util: sleep ms
function sleep(ms) {
  return new Promise((res) => setTimeout(res, ms));
}

// ---------------------------------------------------------------------------
// BatchPage
// ---------------------------------------------------------------------------

export default function BatchPage() {
  const { user } = useAuth();
  const [draft, setDraft] = useState(emptyDraft);
  const [meta, setMeta] = useState(null);
  const [models, setModels] = useState([]);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitProgress, setSubmitProgress] = useState({ done: 0, total: 0 });
  const [drawerBatchId, setDrawerBatchId] = useState(null);
  const [draftSavedAt, setDraftSavedAt] = useState(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  const runningBatches = batchStore.useRunningBatches();
  const sortedBatches = useMemo(
    () => batchStore.sortBatches(runningBatches),
    [runningBatches]
  );

  // Mount: load meta + restore draft from IndexedDB if present.
  useEffect(() => {
    if (!user?.id) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await getModels();
        if (cancelled) return;
        setModels(data.models || []);
        setMeta(data.meta || null);
        batchStore.setMeta(data.meta || null);
      } catch {
        /* ignore */
      }
      try {
        const stored = await getDraft(user.id);
        if (cancelled) return;
        if (stored?.draft) {
          setDraft(stored.draft);
          setDraftSavedAt(stored.saved_at || null);
        }
      } catch {
        /* IndexedDB unavailable — fall back to fresh draft */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  // Autosave to IndexedDB with 600ms debounce.
  useEffect(() => {
    if (!user?.id) return undefined;
    const timer = setTimeout(() => {
      putDraft(user.id, draftRef.current).then(() => {
        setDraftSavedAt(new Date().toISOString());
      }).catch(() => {});
    }, 600);
    return () => clearTimeout(timer);
  }, [draft, user?.id]);

  // Editing
  const updateFixed = (patch) =>
    setDraft((d) => ({ ...d, fixed: { ...d.fixed, ...patch } }));
  const updateSlot = (slotId, next) =>
    setDraft((d) => ({
      ...d,
      slots: d.slots.map((s) =>
        s.slot_id === slotId ? { ...next, rev: (s.rev || 0) + 1 } : s
      ),
    }));
  const addSlot = useCallback(() => {
    setDraft((d) => {
      if (d.slots.length >= MAX_SLOTS) return d;
      const nextIdx = d.slots.reduce(
        (m, s) => Math.max(m, s.stable_idx),
        0
      ) + 1;
      return {
        ...d,
        slots: [
          ...d.slots,
          newSlot(nextIdx, { image_count: d.default_image_count }),
        ],
      };
    });
  }, []);
  const duplicateSlot = (slotId) => {
    setDraft((d) => {
      const idx = d.slots.findIndex((s) => s.slot_id === slotId);
      if (idx === -1) return d;
      if (d.slots.length >= MAX_SLOTS) return d;
      const orig = d.slots[idx];
      const nextIdx = d.slots.reduce(
        (m, s) => Math.max(m, s.stable_idx),
        0
      ) + 1;
      const dup = {
        ...orig,
        slot_id: nano("slt"),
        stable_idx: nextIdx,
        rev: 0,
        set_id: null,
        title: `${orig.title} (copy)`,
      };
      const nextSlots = d.slots.slice();
      nextSlots.splice(idx + 1, 0, dup);
      return { ...d, slots: nextSlots };
    });
  };
  const deleteSlot = (slotId) =>
    setDraft((d) => ({
      ...d,
      slots: d.slots.filter((s) => s.slot_id !== slotId),
    }));

  const totalImages = useMemo(
    () => draft.slots.reduce((acc, s) => acc + s.image_count, 0),
    [draft.slots]
  );

  // Concurrency lock vs. the user's running batches.
  const K = meta?.batch_max_concurrent_per_user || 3;
  const inFlightCount = batchStore.inFlightCount();
  const limitLocked = inFlightCount >= K;
  const totalsCapped = totalImages > MAX_TOTAL_IMAGES;

  // ---- Submit flow ----------------------------------------------------

  const submit = useCallback(async () => {
    if (!draft.slots.length || totalImages === 0) return;
    if (totalsCapped) {
      setError(
        `Total images (${totalImages}) exceeds the cap (${MAX_TOTAL_IMAGES})`
      );
      return;
    }
    setSubmitting(true);
    setError(null);

    // Stage ⓪: freeze the editor snapshot + pre-allocate set_ids so the
    // spec we ship to /api/batches and the per-job set_id we send via
    // /api/jobs agree. Without this lockstep allocation the detail
    // drawer's slot grouping breaks.
    const prepared = prepareDraftForSubmit(draftRef.current);

    let registered = null;
    try {
      registered = await createBatch({
        title: prepared.title || "Untitled batch",
        total_job_count: totalImages,
        spec: specFromDraft(prepared, totalImages),
      });
    } catch (e) {
      setError(e?.message || String(e));
      setSubmitting(false);
      return;
    }

    // Optimistic: insert the registered batch as a top card immediately
    // so the user sees the editor clear + a new card appear together.
    batchStore.upsert(registered);

    const tasks = buildJobTasks(prepared);
    setSubmitProgress({ done: 0, total: tasks.length });

    // Stage ②: clear the editor + the IndexedDB draft. We snapshot
    // ``user.id`` because the closure may run after a logout.
    const userId = user?.id;
    if (userId) {
      clearDraft(userId).catch(() => {});
    }
    setDraft(emptyDraft);

    // Stage ③: fan-out. Pick a model that's available; default to the
    // first one the backend exposes — most users have 1-2 of these.
    const modelId =
      models.find((m) => m.available)?.model_id ||
      models[0]?.model_id ||
      "gpt-image-2";

    let done = 0;
    const concurrency = meta?.batch_concurrency_max || 4;
    const queue = [...tasks];
    const work = async () => {
      while (queue.length) {
        const t = queue.shift();
        if (!t) break;
        try {
          await createJob({
            payload: {
              model: modelId,
              prompt: t.prompt || "(empty)",
              n: 1,
              batch_id: registered.batch_id,
              set_id: t.set_id || undefined,
              client_request_id: t.client_request_id,
            },
            references: [],
          });
        } catch (err) {
          // Don't abort the whole batch on per-job failures —
          // mirror the doc's "failed Job is partial, not abort"
          // semantics. The backend's batch state will end up
          // ``partial`` once everything resolves.
          // eslint-disable-next-line no-console
          console.warn("batch fan-out job failed", err);
        }
        done += 1;
        setSubmitProgress({ done, total: tasks.length });
        // Pacing: tiny stagger so multiple concurrent fan-out
        // workers don't dogpile the SSE bus.
        await sleep(20);
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(concurrency, tasks.length) }, () =>
        work()
      )
    );

    // Stage ④: finalize.
    try {
      await finalizeBatch(registered.batch_id);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("finalize_submission failed", err);
    }
    setSubmitting(false);
    setSubmitProgress({ done: 0, total: 0 });
    batchStore.refresh();
  }, [draft, totalImages, models, meta, user?.id, totalsCapped]);

  // ---- Card actions ---------------------------------------------------

  const handleCardSecondary = useCallback(
    async (batchId, status) => {
      if (status === "running" || status === "submitting") {
        try {
          await cancelBatchQueued(batchId);
          batchStore.refresh();
        } catch (e) {
          setError(e?.message || String(e));
        }
        return;
      }
      if (
        status === "abandoned" ||
        status === "cancelled" ||
        status === "completed"
      ) {
        if (status === "abandoned" || status === "completed") {
          // "Discard" / "Dismiss"
          batchStore.dismiss(batchId);
        }
        if (status === "abandoned") {
          // Server delete on user's request.
          try {
            await deleteBatch(batchId, { keepJobs: true });
          } catch (e) {
            // The batch may not be terminal yet — surface error.
            setError(e?.message || String(e));
          }
          batchStore.refresh();
        }
      }
    },
    []
  );

  // ---- Render ---------------------------------------------------------

  return (
    <div
      data-testid="batch-page"
      style={{
        flex: 1,
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
        position: "relative",
      }}
    >
      <TopBar
        crumb="Create / Batch"
        title={
          <>
            Run a <span style={{ fontStyle: "italic" }}>batch</span>.
          </>
        }
        subtitle="Editor below = local draft. Top list is the server-side truth, shared across devices via SSE."
        right={
          <>
            <span
              className="chip"
              style={{
                background: "#fffdf7",
                border: "1px solid var(--ink)",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                padding: "2px 8px",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  background: "var(--banana-deep)",
                  display: "inline-block",
                }}
              />
              v0.3 · server truth + local draft
            </span>
            <span
              className="chip"
              style={{
                background: "#fffdf7",
                border: "1px solid var(--ink)",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                padding: "2px 8px",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
              data-testid="topbar-in-flight"
            >
              <Icon name="layers" size={10} /> {inFlightCount}/{K} IN FLIGHT
            </span>
          </>
        }
      />

      <RunningBatchList
        batches={sortedBatches}
        onOpen={(id) => setDrawerBatchId(id)}
        onSecondary={handleCardSecondary}
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 340px",
          flex: 1,
          minHeight: 0,
        }}
      >
        <div
          style={{
            padding: "20px 28px 24px",
            borderRight: "1px solid var(--ink)",
            display: "flex",
            flexDirection: "column",
            gap: 18,
            minHeight: 0,
            overflowY: "auto",
          }}
        >
          {/* editor banner */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 10,
              padding: "8px 14px",
              border: "1px dashed var(--ink-3)",
              background: "var(--paper-2)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 10,
              }}
            >
              <input
                data-testid="batch-title"
                value={draft.title || ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, title: e.target.value }))
                }
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 16,
                  fontWeight: 800,
                  letterSpacing: "-0.025em",
                  border: "none",
                  background: "transparent",
                  outline: "none",
                  minWidth: 200,
                }}
              />
              <span
                className="mono caps"
                style={{ fontSize: 9, color: "var(--ink-3)" }}
              >
                local · indexeddb · this device only
              </span>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                type="button"
                onClick={() => {
                  setDraft(emptyDraft);
                  if (user?.id) clearDraft(user.id).catch(() => {});
                }}
                data-testid="clear-draft"
                className="chip"
                style={{
                  border: "1px solid var(--ink)",
                  background: "#fffdf7",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  padding: "2px 8px",
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Icon name="trash" size={9} /> CLEAR DRAFT
              </button>
            </div>
          </div>

          <FixedBlock
            prompt={draft.fixed.prompt}
            appendMode={draft.fixed.append_mode}
            refs={draft.fixed.refs}
            onPromptChange={(prompt) => updateFixed({ prompt })}
            onAppendModeChange={(append_mode) =>
              updateFixed({ append_mode })
            }
            onAddRef={(file) =>
              updateFixed({
                refs: [...(draft.fixed.refs || []), file],
              })
            }
            onRemoveRef={(idx) =>
              updateFixed({
                refs: (draft.fixed.refs || []).filter(
                  (_, i) => i !== idx
                ),
              })
            }
          />

          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              marginTop: 4,
            }}
          >
            <div
              style={{ display: "flex", alignItems: "baseline", gap: 12 }}
            >
              <span
                className="display"
                style={{
                  fontSize: 22,
                  fontWeight: 800,
                  letterSpacing: "-0.025em",
                  fontFamily: "var(--font-display)",
                }}
              >
                Slots
              </span>
              <span
                className="mono caps"
                style={{ fontSize: 10, color: "var(--ink-3)" }}
              >
                {draft.slots.length} slots · {totalImages} images · cap 50 / 400
              </span>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                type="button"
                onClick={addSlot}
                data-testid="add-slot"
                className="btn sm"
                style={{
                  background: "var(--banana)",
                  border: "1px solid var(--ink)",
                  fontSize: 12,
                  padding: "5px 10px",
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontWeight: 600,
                }}
              >
                <Icon name="plus" size={12} /> Add slot
              </button>
            </div>
          </div>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            {draft.slots.map((s) => (
              <SlotCard
                key={s.slot_id}
                slot={s}
                fixedRefsCount={(draft.fixed.refs || []).length}
                onChange={(next) => updateSlot(s.slot_id, next)}
                onDuplicate={() => duplicateSlot(s.slot_id)}
                onDelete={() => deleteSlot(s.slot_id)}
              />
            ))}

            {draft.slots.length === 0 && (
              <div
                data-testid="empty-editor"
                style={{
                  padding: "32px 16px",
                  border: "1px dashed var(--ink-3)",
                  background: "var(--paper-2)",
                  textAlign: "center",
                }}
              >
                <div
                  className="display"
                  style={{
                    fontSize: 18,
                    fontWeight: 800,
                    letterSpacing: "-0.025em",
                    marginBottom: 4,
                    fontFamily: "var(--font-display)",
                  }}
                >
                  Editor cleared.
                </div>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    color: "var(--ink-3)",
                    lineHeight: 1.5,
                  }}
                >
                  add a slot to start a fresh batch — your last global params
                  are still set as defaults.
                </div>
              </div>
            )}

            <button
              type="button"
              onClick={addSlot}
              style={{
                padding: "16px",
                border: "1px dashed var(--ink-3)",
                background: "transparent",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                color: "var(--ink-3)",
                fontFamily: "var(--font-sans)",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              <Icon name="plus" size={14} /> Add slot
            </button>
          </div>
        </div>

        <RightRail
          sessionStrategy={draft.session_strategy}
          defaultImageCount={draft.default_image_count}
          onSessionStrategyChange={(session_strategy) =>
            setDraft((d) => ({ ...d, session_strategy }))
          }
          onDefaultImageCountChange={(default_image_count) =>
            setDraft((d) => ({ ...d, default_image_count }))
          }
        />
      </div>

      <ActionBar
        slotCount={draft.slots.length}
        imageCount={totalImages}
        batchesInFlight={inFlightCount}
        K={K}
        locked={limitLocked}
        submitting={submitting}
        submittingProgress={submitProgress}
        onValidate={() => {
          if (totalImages === 0) {
            setError("Add at least one slot before submitting.");
          } else if (totalsCapped) {
            setError(
              `Total images (${totalImages}) exceeds the cap (${MAX_TOTAL_IMAGES})`
            );
          } else {
            setError(null);
          }
        }}
        onSubmit={submit}
      />

      {drawerBatchId && (
        <BatchDetailDrawer
          batchId={drawerBatchId}
          onClose={() => setDrawerBatchId(null)}
        />
      )}

      {error && (
        <div
          data-testid="batch-error"
          style={{
            position: "absolute",
            top: 16,
            right: 16,
            padding: "10px 14px",
            background: "var(--bad)",
            color: "white",
            border: "1px solid var(--ink)",
            zIndex: 40,
            maxWidth: 380,
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            cursor: "pointer",
          }}
          onClick={() => setError(null)}
        >
          {error}
        </div>
      )}

      {!error && draftSavedAt && draft.slots.length > 0 && (
        <span style={{ display: "none" }} data-testid="draft-saved-at">
          {draftSavedAt}
        </span>
      )}
    </div>
  );
}
