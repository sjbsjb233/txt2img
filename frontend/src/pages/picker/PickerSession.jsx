// PickerSession — the judging page (PRD §4.2 / §4.4).
//
// Composes header / compare surface / contact sheet / filmstrip /
// judgment row / right meta-rail. Wires the keyboard shortcuts and
// renders the four sub-states (empty / judging / finalized / confirm
// modal) by branching on the bundle state.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import AuthorizedImage from "../../components/AuthorizedImage.jsx";
import ComparePane from "../../components/picker/ComparePane.jsx";
import Filmstrip from "../../components/picker/Filmstrip.jsx";
import FinalizeConfirmModal from "../../components/picker/FinalizeConfirmModal.jsx";
import FullscreenView from "../../components/picker/FullscreenView.jsx";
import JudgmentRow from "../../components/picker/JudgmentRow.jsx";
import {
  InFlightCard,
  MetaRail,
  OutcomeBlock,
  RailSection,
} from "../../components/picker/MetaRail.jsx";
import SessionsDrawer from "../../components/picker/SessionsDrawer.jsx";
import * as pickerStore from "../../store/picker.js";

const STATE = pickerStore.STATE;

function statsOf(bundle) {
  const stats = {
    final: 0,
    picked: 0,
    discarded: 0,
    deferred: 0,
    unjudged: 0,
  };
  if (!bundle) return { ...stats, total: 0 };
  for (const im of bundle.images.values()) {
    const k = im.pick_state || "unjudged";
    if (stats[k] !== undefined) stats[k] += 1;
  }
  return { ...stats, total: bundle.images.size };
}

function findNextUnjudgedIdx(images, fromIdx) {
  for (let i = fromIdx + 1; i < images.length; i += 1) {
    if (images[i].pick_state === STATE.UNJUDGED) return i;
  }
  for (let i = 0; i < fromIdx; i += 1) {
    if (images[i].pick_state === STATE.UNJUDGED) return i;
  }
  for (let i = 0; i < images.length; i += 1) {
    if (images[i].pick_state === STATE.DEFERRED) return i;
  }
  return fromIdx;
}

export default function PickerSession({ snapshot, userId }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get("session_id");

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [compareMode, setCompareMode] = useState("vs-final");
  const [cursorIdx, setCursorIdx] = useState(0);
  const [pendingFinalImage, setPendingFinalImage] = useState(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [, setTick] = useState(0);
  const judgeRef = useRef(null);

  // Mount the session bundle.
  useEffect(() => {
    if (!sessionId || !userId) return;
    void pickerStore.openSession(userId, sessionId);
    return () => {
      pickerStore.closeSession();
    };
  }, [sessionId, userId]);

  const bundle = snapshot.bundle;
  const isCorrectBundle = bundle?.session?.id === sessionId;

  const images = useMemo(
    () => (isCorrectBundle ? Array.from(bundle.images.values()) : []),
    [bundle, isCorrectBundle]
  );

  // Restore cursor from session metadata on first mount of this bundle.
  const lastSeenSessionId = useRef(null);
  useEffect(() => {
    if (!isCorrectBundle) return;
    if (lastSeenSessionId.current === sessionId) return;
    lastSeenSessionId.current = sessionId;
    if (images.length === 0) {
      setCursorIdx(0);
      return;
    }
    const cursorImageId = bundle.session?.cursor_image_id;
    if (cursorImageId) {
      const idx = images.findIndex((im) => im.image_id === cursorImageId);
      if (idx >= 0) {
        setCursorIdx(idx);
        return;
      }
    }
    const firstUnjudged = images.findIndex(
      (im) => im.pick_state === STATE.UNJUDGED
    );
    setCursorIdx(firstUnjudged >= 0 ? firstUnjudged : 0);
  }, [bundle, isCorrectBundle, sessionId, images]);

  // Persist cursor changes (throttled inside the store).
  useEffect(() => {
    if (!isCorrectBundle || images.length === 0) return;
    const cur = images[cursorIdx];
    if (cur) pickerStore.setCursor(sessionId, cur.image_id);
  }, [cursorIdx, images, sessionId, isCorrectBundle]);

  const cur = images[cursorIdx] || null;
  const finalImg = bundle?.session?.final_image_id
    ? bundle.images.get(bundle.session.final_image_id)
    : null;
  const stats = useMemo(() => statsOf(isCorrectBundle ? bundle : null), [
    bundle,
    isCorrectBundle,
  ]);

  const inFlightCounts = useMemo(() => {
    const counts = { queued: 0, running: 0, failed: 0 };
    if (!isCorrectBundle) return counts;
    for (const job of bundle.jobs || []) {
      if (job.status === "QUEUED") counts.queued += 1;
      else if (job.status === "RUNNING") counts.running += 1;
      else if (job.status === "FAILED") counts.failed += 1;
    }
    return counts;
  }, [bundle, isCorrectBundle]);
  const totalInflight =
    inFlightCounts.queued + inFlightCounts.running + inFlightCounts.failed;

  const isFinalized = bundle?.session?.picker_state === "finalized";
  const isReady = isCorrectBundle
    ? pickerStore.isReadyToFinalize(bundle)
    : false;
  const isEmpty = isCorrectBundle && images.length === 0;

  const judge = useCallback(
    async (kind) => {
      if (!cur || isFinalized) return;
      // F when there's already a final triggers the swap modal.
      if (
        kind === STATE.FINAL &&
        bundle?.session?.final_image_id &&
        bundle.session.final_image_id !== cur.image_id
      ) {
        setPendingFinalImage(cur);
        return;
      }
      try {
        await pickerStore.judgeImage(cur, kind);
        // Auto-advance.
        const nextImages = Array.from(bundle.images.values());
        const nextIdx = findNextUnjudgedIdx(nextImages, cursorIdx);
        if (nextIdx !== cursorIdx) setCursorIdx(nextIdx);
      } catch (e) {
        // Swallow — store already shows toast / re-pulled.
      }
    },
    [cur, bundle, cursorIdx, isFinalized]
  );

  judgeRef.current = judge;

  // Keyboard handler — global.
  useEffect(() => {
    if (!isCorrectBundle) return undefined;
    const onKey = (e) => {
      const t = e.target;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      ) {
        return;
      }
      if (pendingFinalImage || drawerOpen) {
        if (e.key === "Escape") {
          if (pendingFinalImage) setPendingFinalImage(null);
          else setDrawerOpen(false);
        }
        if (pendingFinalImage && e.key === "Enter") {
          e.preventDefault();
          confirmFinalSwap();
        }
        return;
      }

      if (e.key === "Escape") {
        if (fullscreen) {
          setFullscreen(false);
          return;
        }
        navigate("/picker");
        return;
      }
      if (e.key === "ArrowLeft" || e.key.toLowerCase() === "k") {
        setCursorIdx((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "ArrowRight" || e.key.toLowerCase() === "j") {
        const nextImages = Array.from(bundle.images.values());
        const cur2 = nextImages[cursorIdx];
        if (cur2 && cur2.pick_state === STATE.UNJUDGED) {
          // Advancing past an unjudged image marks it as deferred.
          judgeRef.current(STATE.DEFERRED);
        } else {
          setCursorIdx((i) => Math.min(nextImages.length - 1, i + 1));
        }
        return;
      }
      if (e.key === "Home") {
        setCursorIdx(0);
        return;
      }
      if (e.key === "End") {
        setCursorIdx(images.length - 1);
        return;
      }
      if (e.key.toLowerCase() === "p") {
        judgeRef.current(STATE.PICKED);
        return;
      }
      if (e.key.toLowerCase() === "x") {
        judgeRef.current(STATE.DISCARDED);
        return;
      }
      if (e.key.toLowerCase() === "f") {
        judgeRef.current(STATE.FINAL);
        return;
      }
      if (e.key === " ") {
        e.preventDefault();
        judgeRef.current(STATE.DEFERRED);
        return;
      }
      if (e.key.toLowerCase() === "u") {
        void pickerStore.undoLast().then(() => setTick((n) => n + 1));
        return;
      }
      if (e.key.toLowerCase() === "s") {
        setDrawerOpen((o) => !o);
        return;
      }
      if (e.key === "Enter" && isReady) {
        void pickerStore.finalizeSession(sessionId);
        return;
      }
      const numeric = Number(e.key);
      if (!Number.isNaN(numeric) && numeric >= 1 && numeric <= 9) {
        const target = snapshot.overview[numeric - 1];
        if (target) {
          setDrawerOpen(false);
          navigate(`/picker?session_id=${encodeURIComponent(target.id)}`);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    bundle,
    cursorIdx,
    drawerOpen,
    fullscreen,
    images.length,
    isCorrectBundle,
    isReady,
    navigate,
    pendingFinalImage,
    sessionId,
    snapshot.overview,
  ]);

  const confirmFinalSwap = useCallback(async () => {
    if (!pendingFinalImage) return;
    try {
      await pickerStore.judgeImage(pendingFinalImage, STATE.FINAL);
      setPendingFinalImage(null);
      const nextImages = Array.from(bundle.images.values());
      const nextIdx = findNextUnjudgedIdx(nextImages, cursorIdx);
      if (nextIdx !== cursorIdx) setCursorIdx(nextIdx);
    } catch {
      setPendingFinalImage(null);
    }
  }, [bundle, cursorIdx, pendingFinalImage]);

  const cancelFinalSwap = useCallback(() => setPendingFinalImage(null), []);

  // Sessions drawer interactions.
  const onSelectSession = useCallback(
    (id) => {
      setDrawerOpen(false);
      if (id !== sessionId) {
        navigate(`/picker?session_id=${encodeURIComponent(id)}`);
      }
    },
    [navigate, sessionId]
  );

  const onNewSession = useCallback(async () => {
    const name = window.prompt("Session name?");
    if (!name) return;
    try {
      const created = await pickerStore.createNewSession(name.trim());
      setDrawerOpen(false);
      navigate(`/picker?session_id=${encodeURIComponent(created.id)}`);
    } catch (e) {
      window.alert(e?.message || "Could not create session");
    }
  }, [navigate]);

  if (!sessionId) {
    return null; // parent renders DeckOverview when no session_id
  }

  if (!isCorrectBundle) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--ink-3)",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
        }}
      >
        loading session…
      </div>
    );
  }

  const session = bundle.session;
  const stateChip = renderStateChip(session, isReady);
  const judgedLabel = `${stats.picked + stats.discarded + stats.final}/${stats.total} judged${
    totalInflight ? ` · ${totalInflight} in flight` : ""
  }`;

  return (
    <>
      <div
        data-testid="picker-judging-page"
        style={{
          flex: 1,
          background: "var(--paper)",
          display: "grid",
          gridTemplateColumns: "1fr 280px",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* MAIN COLUMN */}
        <div
          style={{
            display: "grid",
            gridTemplateRows: "auto 1fr auto auto auto",
            minWidth: 0,
            position: "relative",
            overflow: "hidden",
          }}
        >
          <PickerHeader
            sessionName={session.name}
            stateChip={stateChip}
            judgedLabel={judgedLabel}
            locked={isFinalized}
            compareMode={compareMode}
            onCompareModeChange={setCompareMode}
            onSessionsClick={() => setDrawerOpen((o) => !o)}
            onFullscreen={() => setFullscreen(true)}
            onBack={() => navigate("/picker")}
            isReady={isReady}
            onFinalize={() => pickerStore.finalizeSession(sessionId)}
          />

          <CompareSurface
            mode={compareMode}
            cursor={cur}
            cursorIdx={cursorIdx}
            total={images.length}
            previous={images[Math.max(0, cursorIdx - 1)]}
            finalImg={finalImg}
            isFinalized={isFinalized}
            isEmpty={isEmpty}
            inFlightCounts={inFlightCounts}
            session={session}
            onUnfinalize={() => pickerStore.unfinalizeSession(sessionId)}
            stats={stats}
          />

          {!isEmpty && !isFinalized && (
            <ContactSheet
              images={images}
              cursorIdx={cursorIdx}
              onPick={setCursorIdx}
              stats={stats}
            />
          )}

          <FilmstripBlock
            images={images}
            cursorIdx={cursorIdx}
            setCursorIdx={setCursorIdx}
            inFlightCounts={inFlightCounts}
            isFinalized={isFinalized}
          />

          <JudgmentRow
            disabled={isFinalized || isEmpty || !cur}
            onPick={() => judge(STATE.PICKED)}
            onDiscard={() => judge(STATE.DISCARDED)}
            onDefer={() => judge(STATE.DEFERRED)}
            onFinal={() => judge(STATE.FINAL)}
            onUndo={() =>
              pickerStore.undoLast().then(() => setTick((n) => n + 1))
            }
          />

          <SessionsDrawer
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            activeId={sessionId}
            sessions={snapshot.overview}
            onSelect={onSelectSession}
            onNewSession={onNewSession}
          />

          {pendingFinalImage && (
            <FinalizeConfirmModal
              currentFinal={finalImg}
              candidate={pendingFinalImage}
              onCancel={cancelFinalSwap}
              onConfirm={confirmFinalSwap}
            />
          )}
        </div>

        {/* RIGHT META RAIL */}
        <RightRail
          stats={stats}
          session={session}
          jobs={bundle.jobs}
          cur={cur}
          totalInflight={totalInflight}
          isFinalized={isFinalized}
        />
      </div>

      {fullscreen && (
        <FullscreenView
          bundle={bundle}
          cursorIdx={cursorIdx}
          setCursorIdx={setCursorIdx}
          inFlightCounts={inFlightCounts}
          onExit={() => setFullscreen(false)}
        />
      )}
    </>
  );
}

function renderStateChip(session, isReady) {
  if (session.picker_state === "finalized") {
    return <span className="pk-chip solid">★ FINALIZED · LOCKED</span>;
  }
  if (isReady) {
    return (
      <span className="pk-chip ok">
        <span className="pk-state-dot final" /> READY · F to finalize
      </span>
    );
  }
  if (session.picker_state === "judging") {
    return (
      <span className="pk-chip">
        <span className="pk-state-dot final" /> judging
      </span>
    );
  }
  return (
    <span className="pk-chip">
      <span className="pk-state-dot unjudged" /> not started
    </span>
  );
}

function PickerHeader({
  sessionName,
  stateChip,
  judgedLabel,
  locked,
  compareMode,
  onCompareModeChange,
  onSessionsClick,
  onFullscreen,
  onBack,
  isReady,
  onFinalize,
}) {
  return (
    <div
      style={{
        padding: "12px 22px",
        borderBottom: "1px solid var(--ink)",
        background: locked ? "var(--banana)" : "var(--paper-soft)",
        display: "flex",
        gap: 14,
        alignItems: "center",
      }}
    >
      <button
        className="btn sm"
        onClick={onBack}
        type="button"
        title="Back to deck (Esc)"
      >
        ← Deck
      </button>
      <button
        className="btn sm"
        onClick={onSessionsClick}
        type="button"
        title="Sessions (S)"
        disabled={locked}
        data-testid="picker-sessions-button"
        style={locked ? { opacity: 0.5 } : {}}
      >
        ☰ Sessions <span className="kbd" style={{ marginLeft: 4 }}>S</span>
      </button>
      <span
        style={{
          width: 1,
          height: 22,
          background: "var(--ink)",
          opacity: 0.18,
        }}
      />
      <span
        className="caps"
        style={{ fontSize: 11, color: "var(--ink-3)" }}
      >
        Picker
      </span>
      <span
        className="display"
        style={{ fontSize: 22, fontStyle: "italic", fontWeight: 500 }}
      >
        {sessionName}
      </span>
      {stateChip}
      <span
        className="mono"
        style={{
          fontSize: 10,
          color: locked ? "var(--ink)" : "var(--ink-3)",
          marginLeft: 4,
        }}
      >
        {judgedLabel}
      </span>
      <div style={{ flex: 1 }} />
      {isReady && !locked && (
        <button
          className="btn sm primary shadowed"
          onClick={onFinalize}
          type="button"
        >
          ★ Finalize · <span className="kbd">↵</span>
        </button>
      )}
      {!locked && (
        <div style={{ display: "flex", border: "1px solid var(--ink)" }}>
          <button
            className="btn sm ghost"
            type="button"
            style={{
              border: "none",
              background:
                compareMode === "vs-final" ? "var(--ink)" : "transparent",
              color: compareMode === "vs-final" ? "var(--paper)" : "var(--ink)",
            }}
            onClick={() => onCompareModeChange("vs-final")}
          >
            vs FINAL
          </button>
          <button
            className="btn sm ghost"
            type="button"
            style={{
              border: "none",
              borderLeft: "1px solid var(--ink)",
              background:
                compareMode === "side-by-side" ? "var(--ink)" : "transparent",
              color:
                compareMode === "side-by-side" ? "var(--paper)" : "var(--ink)",
            }}
            onClick={() => onCompareModeChange("side-by-side")}
          >
            Pair
          </button>
        </div>
      )}
      <button
        className="btn sm"
        onClick={onFullscreen}
        type="button"
        disabled={locked}
        data-testid="picker-fullscreen-button"
        style={locked ? { opacity: 0.5 } : {}}
      >
        ⛶ Fullscreen
      </button>
    </div>
  );
}

function CompareSurface({
  mode,
  cursor,
  cursorIdx,
  total,
  previous,
  finalImg,
  isFinalized,
  isEmpty,
  inFlightCounts,
  session,
  onUnfinalize,
  stats,
}) {
  if (isEmpty) {
    return <EmptyCompareSurface inFlightCounts={inFlightCounts} />;
  }
  if (isFinalized) {
    return (
      <FinalizedCompareSurface
        finalImg={finalImg}
        session={session}
        stats={stats}
        onUnfinalize={onUnfinalize}
      />
    );
  }
  return (
    <div
      style={{
        padding: 22,
        background: "var(--paper)",
        minHeight: 0,
        display: "grid",
        gridTemplateColumns: mode === "vs-final" ? "1fr auto 1fr" : "1fr 1fr",
        gap: 14,
        alignItems: "stretch",
      }}
    >
      <ComparePane
        label={mode === "vs-final" ? "CURRENT FINAL" : "← Previous"}
        image={mode === "vs-final" ? finalImg : previous}
        sub={
          mode === "vs-final"
            ? "the slide will use this"
            : "press ← to step"
        }
      />
      {mode === "vs-final" && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
          }}
        >
          <span
            className="display"
            style={{
              fontSize: 16,
              fontStyle: "italic",
              color: "var(--ink-3)",
              letterSpacing: "0.02em",
            }}
          >
            vs.
          </span>
        </div>
      )}
      <ComparePane
        label={`CANDIDATE · #${cursorIdx + 1}/${total}`}
        image={cursor}
        sub={
          cursor?.pick_state === "unjudged"
            ? "awaiting judgment"
            : cursor?.pick_state
        }
        tone="banana"
        big
      />
    </div>
  );
}

function EmptyCompareSurface({ inFlightCounts }) {
  return (
    <div
      className="pk-hatch"
      style={{
        margin: 22,
        border: "1.5px dashed var(--ink-3)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 18,
        padding: 40,
      }}
    >
      <div
        className="display"
        style={{ fontStyle: "italic", fontSize: 38, textAlign: "center" }}
      >
        No images yet
      </div>
      <div
        style={{
          color: "var(--ink-3)",
          textAlign: "center",
          maxWidth: 480,
          fontSize: 14,
          lineHeight: 1.5,
        }}
      >
        {inFlightCounts.queued + inFlightCounts.running > 0
          ? "Jobs are running for this session. They'll land here as they complete — usually 8–30 seconds each. The first to finish becomes the candidate."
          : "Head over to the Create page to generate the first image for this session."}
      </div>
    </div>
  );
}

function FinalizedCompareSurface({ finalImg, session, stats, onUnfinalize }) {
  return (
    <div
      style={{
        padding: 22,
        background: "var(--paper)",
        minHeight: 0,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 14,
        alignItems: "stretch",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          minHeight: 0,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
          }}
        >
          <span
            className="caps"
            style={{
              fontSize: 10,
              background: "var(--banana)",
              color: "var(--ink)",
              padding: "3px 8px",
            }}
          >
            ★ FINAL
          </span>
          {finalImg && (
            <span
              className="mono"
              style={{ fontSize: 10, color: "var(--ink-3)" }}
            >
              {finalImg.image_id}
            </span>
          )}
        </div>
        <div
          className="pk-img-pane current"
          style={{
            flex: 1,
            minHeight: 0,
            position: "relative",
            aspectRatio: finalImg
              ? `${finalImg.width} / ${finalImg.height}`
              : "1 / 1",
            alignSelf: "center",
            maxWidth: "100%",
          }}
        >
          {finalImg && (
            <AuthorizedImage
              src={finalImg.thumb_url}
              alt=""
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
            />
          )}
          <div className="pk-img-tag banana">★ FINAL · in slide</div>
        </div>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 14,
          minHeight: 0,
        }}
      >
        <div
          style={{
            border: "1px solid var(--ink)",
            background: "var(--paper-soft)",
            padding: 16,
          }}
        >
          <span
            className="caps"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            Outcome
          </span>
          <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
            {[
              { lbl: "Final", n: stats.final, c: "final" },
              { lbl: "Picked", n: stats.picked, c: "picked" },
              { lbl: "Discarded", n: stats.discarded, c: "discarded" },
            ].map((r) => (
              <div
                key={r.lbl}
                style={{
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto",
                  gap: 8,
                  alignItems: "center",
                  padding: "6px 0",
                  borderBottom: "1px dashed var(--rule-2)",
                }}
              >
                <span className={`pk-state-dot ${r.c}`} />
                <span style={{ fontSize: 14 }}>{r.lbl}</span>
                <span
                  className="mono"
                  style={{ fontSize: 14, fontWeight: 700 }}
                >
                  {r.n}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div
          style={{
            border: "1px solid var(--ink)",
            background: "var(--paper-soft)",
            padding: 16,
          }}
        >
          <span
            className="caps"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            Decision summary
          </span>
          <p
            style={{
              margin: "8px 0 0",
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 14,
              lineHeight: 1.5,
              color: "var(--ink-2)",
            }}
          >
            “{session.name}” → {stats.total} generated, 1 selected.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className="btn sm"
            type="button"
            onClick={onUnfinalize}
          >
            ↺ Reopen for judging
          </button>
        </div>
      </div>
    </div>
  );
}

function ContactSheet({ images, cursorIdx, onPick, stats }) {
  const start = Math.max(0, cursorIdx - 1);
  const sheet = images.slice(start, start + 8);
  return (
    <div style={{ padding: "0 22px 14px", background: "var(--paper)" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 8,
        }}
      >
        <span className="caps" style={{ fontSize: 10 }}>
          Up next · contact sheet
        </span>
        <span
          className="mono"
          style={{ fontSize: 10, color: "var(--ink-3)" }}
        >
          {stats.unjudged} unjudged remaining
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(8, 1fr)",
          gap: 8,
        }}
      >
        {sheet.map((im) => {
          const realIdx = images.findIndex((x) => x.image_id === im.image_id);
          const isCur = realIdx === cursorIdx;
          return (
            <button
              key={im.image_id}
              onClick={() => onPick(realIdx)}
              type="button"
              style={{
                all: "unset",
                cursor: "pointer",
                aspectRatio: "1 / 1",
                border: isCur
                  ? "2px solid var(--banana-deep)"
                  : "1px solid var(--ink)",
                position: "relative",
                boxShadow: isCur ? "3px 3px 0 var(--ink)" : "none",
                background: "var(--paper-3)",
                overflow: "hidden",
              }}
            >
              <AuthorizedImage
                src={im.thumb_url}
                alt=""
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  display: "block",
                }}
              />
              <span
                style={{ position: "absolute", top: 4, left: 4 }}
                className={`pk-state-dot ${im.pick_state}`}
              />
              <span
                style={{
                  position: "absolute",
                  bottom: 0,
                  right: 0,
                  background: "var(--ink)",
                  color: "var(--paper)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  padding: "1px 4px",
                }}
              >
                #{realIdx + 1}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function FilmstripBlock({
  images,
  cursorIdx,
  setCursorIdx,
  inFlightCounts,
  isFinalized,
}) {
  return (
    <div
      style={{
        borderTop: "1px solid var(--ink)",
        background: "var(--paper-soft)",
      }}
    >
      <div
        style={{
          padding: "8px 22px 0",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span className="caps" style={{ fontSize: 10 }}>
          Filmstrip
        </span>
        <span
          className="mono"
          style={{ fontSize: 10, color: "var(--ink-3)" }}
        >
          {isFinalized
            ? "locked · judging done"
            : "hover to preview · click to jump"}
        </span>
      </div>
      <Filmstrip
        images={images}
        cursorIdx={cursorIdx}
        onPick={isFinalized ? undefined : setCursorIdx}
        inflightR={inFlightCounts.running}
        inflightQ={inFlightCounts.queued}
        inflightF={inFlightCounts.failed}
      />
    </div>
  );
}

function RightRail({ stats, session, jobs, cur, totalInflight, isFinalized }) {
  const lastSucceededJob =
    jobs.find((j) => j.status === "SUCCEEDED") || jobs[0] || null;
  const inFlightJobs = jobs.filter((j) =>
    ["QUEUED", "RUNNING", "FAILED"].includes(j.status)
  );
  return (
    <MetaRail>
      <RailSection title="Outcome">
        <OutcomeBlock stats={stats} />
      </RailSection>
      <RailSection title="Prompt">
        {lastSucceededJob ? (
          <p
            style={{
              margin: 0,
              fontFamily: "var(--font-display)",
              fontSize: 13,
              lineHeight: 1.4,
              fontStyle: "italic",
              color: "var(--ink-2)",
              textWrap: "pretty",
            }}
          >
            “{lastSucceededJob.prompt}”
          </p>
        ) : (
          <div
            style={{
              fontSize: 11,
              color: "var(--ink-3)",
              fontFamily: "var(--font-mono)",
            }}
          >
            no prompt yet
          </div>
        )}
      </RailSection>
      <RailSection title="Image" sub={cur ? `${cur.width}×${cur.height}` : "—"}>
        {cur ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              rowGap: 4,
              columnGap: 8,
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              color: "var(--ink-2)",
            }}
          >
            <span style={{ color: "var(--ink-3)" }}>id</span>
            <span style={{ wordBreak: "break-all" }}>{cur.image_id}</span>
            {cur.seed && (
              <>
                <span style={{ color: "var(--ink-3)" }}>seed</span>
                <span>{cur.seed}</span>
              </>
            )}
            <span style={{ color: "var(--ink-3)" }}>job</span>
            <span>
              #{(cur.job_idx ?? 0) + 1} · slot {cur.order}
            </span>
            <span style={{ color: "var(--ink-3)" }}>state</span>
            <span
              style={{
                fontWeight: 700,
                color:
                  cur.pick_state === "final"
                    ? "var(--banana-deep)"
                    : "var(--ink-2)",
              }}
            >
              {cur.pick_state}
            </span>
          </div>
        ) : (
          <div
            style={{
              fontSize: 11,
              color: "var(--ink-3)",
              fontFamily: "var(--font-mono)",
            }}
          >
            no candidate yet
          </div>
        )}
      </RailSection>
      <RailSection
        title="In flight"
        sub={`${totalInflight} active`}
        grow
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {inFlightJobs.length === 0 && (
            <div
              style={{
                fontSize: 11,
                color: "var(--ink-3)",
                fontFamily: "var(--font-mono)",
              }}
            >
              idle
            </div>
          )}
          {inFlightJobs.map((job) => (
            <InFlightCard
              key={job.hash_id}
              kind={
                job.status === "RUNNING"
                  ? "running"
                  : job.status === "QUEUED"
                  ? "queued"
                  : "failed"
              }
              label={job.status}
              value={
                job.status === "RUNNING"
                  ? "in progress"
                  : job.status === "FAILED"
                  ? "retry"
                  : "—"
              }
              pct={job.status === "RUNNING" ? 50 : 0}
              prompt={job.prompt}
            />
          ))}
        </div>
      </RailSection>
      <div
        style={{
          padding: "10px 14px",
          borderTop: "1px solid var(--ink)",
          background: "var(--paper-3)",
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        <button
          className="btn sm"
          type="button"
          disabled={!cur || isFinalized}
          onClick={() => {
            if (!cur) return;
            window.location.href = `/create?ref_image=${encodeURIComponent(
              cur.image_id
            )}`;
          }}
        >
          ↧ Use as ref
        </button>
        <button
          className="btn sm"
          type="button"
          disabled
          title="Coming soon"
        >
          ⌖ Vary seed
        </button>
        <button
          className="btn sm"
          type="button"
          onClick={() => {
            if (session?.id) {
              window.location.href = `/create?session_id=${encodeURIComponent(
                session.id
              )}`;
            }
          }}
        >
          ＋ Generate
        </button>
      </div>
    </MetaRail>
  );
}
