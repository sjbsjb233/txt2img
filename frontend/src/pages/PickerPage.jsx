// Picker page — judge generated images one at a time and lock in a
// final per session. PRD §4 (boards 01-05) spec.
//
// Single file because the boards share a lot of state (session +
// cursor + undo stack + drawer toggles) and splitting would create a
// prop-drilling tax. Sub-components are defined below the main page.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../store/auth.js";
import * as picker from "../store/picker.js";
import * as queue from "../store/pickerImageQueue.js";
import PickerImage from "../components/PickerImage.jsx";
import { createSession } from "../api/sessions.js";

// ---------------------------------------------------------------------
// Top-level switcher: deck overview vs. session view
// ---------------------------------------------------------------------

export default function PickerPage() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const sessionIdParam = params.get("session_id");
  const [fullscreen, setFullscreen] = useState(false);

  // Mount overview store once we have a user.
  useEffect(() => {
    if (user?.id) void picker.mountOverview(user.id);
    return () => {
      // We deliberately don't fully unmount on Picker-route leave —
      // overview cache should survive a quick "back" navigation.
    };
  }, [user?.id]);

  // Switch to session view when query has session_id.
  useEffect(() => {
    if (sessionIdParam) {
      void picker.openSession(sessionIdParam);
    } else {
      picker.closeSession();
    }
  }, [sessionIdParam]);

  // Wire fullscreen toggle event from the header.
  useEffect(() => {
    const onEnter = () => setFullscreen(true);
    const onExit = () => setFullscreen(false);
    window.addEventListener("picker:fullscreen", onEnter);
    window.addEventListener("picker:exit-fullscreen", onExit);
    return () => {
      window.removeEventListener("picker:fullscreen", onEnter);
      window.removeEventListener("picker:exit-fullscreen", onExit);
    };
  }, []);

  // Esc in fullscreen returns to the regular session view.
  useEffect(() => {
    if (!fullscreen) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setFullscreen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  // Cleanup queue on hard unmount only.
  useEffect(() => {
    return () => {
      queue.clearAll();
    };
  }, []);

  if (sessionIdParam) {
    if (fullscreen) {
      return (
        <FullscreenView
          sessionId={sessionIdParam}
          onExit={() => setFullscreen(false)}
        />
      );
    }
    return <SessionView sessionId={sessionIdParam} />;
  }
  return <DeckOverview />;
}

// ---------------------------------------------------------------------
// Board 01 — Deck overview
// ---------------------------------------------------------------------

function DeckOverview() {
  const navigate = useNavigate();
  const ov = picker.usePickerOverview();
  const data = ov.data;

  // Esc on overview is a no-op; stays here.

  const stats = data ? data.summary : null;
  const totals = data ? data.totals : null;
  const sessions = data?.sessions || [];

  return (
    <div
      className="picker-root"
      style={{
        height: "100%",
        overflow: "auto",
        padding: "20px 28px 36px",
      }}
      data-testid="picker-deck-overview"
    >
      {/* Top deck-identity strip */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          paddingBottom: 8,
          borderBottom: "1px solid var(--ink)",
          marginBottom: 18,
        }}
      >
        <span className="caps mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Picker · Deck Overview
        </span>
        <span className="pkchip">
          {sessions.length} session{sessions.length === 1 ? "" : "s"}
        </span>
        <div style={{ flex: 1 }} />
        <button
          className="pkbtn sm"
          disabled={!data || stats?.finalized === 0}
          onClick={() =>
            window.alert("Export deck — generates a zip of all finalized session finals.")
          }
        >
          ⤓ Export deck
        </button>
        {stats && stats.ready_to_finalize > 0 && (
          <button
            className="pkbtn sm shadowed primary"
            onClick={() =>
              window.alert(
                `${stats.ready_to_finalize} session(s) are ready. Open one to finalize.`
              )
            }
          >
            ★ Finalize all ready ({stats.ready_to_finalize})
          </button>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <div
            className="display"
            data-testid="picker-deck-title"
            style={{ fontSize: 28, fontStyle: "italic", lineHeight: 1.1 }}
          >
            {data?.deck_title || "Untitled deck"}
          </div>
          <div
            className="mono"
            style={{
              fontSize: 11,
              color: "var(--ink-3)",
              marginTop: 6,
              letterSpacing: "0.05em",
            }}
          >
            {totals
              ? `${totals.judged}/${totals.images} images judged · ${totals.inflight} in flight`
              : "—"}
          </div>
        </div>
      </div>

      {/* 5-column stats strip */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(5, 1fr)",
          gap: 0,
          marginTop: 18,
          border: "1px solid var(--ink)",
        }}
      >
        <StatCol
          testid="picker-deck-stat-total"
          label="Total images"
          value={totals?.images ?? 0}
          sub={`across ${sessions.length} session${sessions.length === 1 ? "" : "s"}`}
        />
        <StatCol
          testid="picker-deck-stat-finalized"
          label="Finalized"
          value={stats?.finalized ?? 0}
          sub="★ locked in slide"
          dot="final"
        />
        <StatCol
          testid="picker-deck-stat-ready"
          label="Ready to finalize"
          value={stats?.ready_to_finalize ?? 0}
          sub="1 click to lock"
          accent
        />
        <StatCol
          testid="picker-deck-stat-inProgress"
          label="In progress"
          value={stats?.judging ?? 0}
          sub={`${totals?.judged ?? 0} judged so far`}
          dot="picked"
        />
        <StatCol
          testid="picker-deck-stat-notStarted"
          label="Not started"
          value={stats?.not_started ?? 0}
          sub={`${totals?.inflight ?? 0} jobs in flight`}
          dot="unjudged"
        />
      </div>

      {/* Session grid */}
      {sessions.length > 0 ? (
        <div
          style={{
            marginTop: 22,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 16,
          }}
          data-testid="picker-session-grid"
        >
          {sessions.map((s, idx) => (
            <DeckSessionCard
              key={s.id}
              session={s}
              idx={idx}
              onOpen={() => navigate(`/picker?session_id=${encodeURIComponent(s.id)}`)}
            />
          ))}
        </div>
      ) : (
        <div
          className="hatch"
          style={{
            marginTop: 60,
            border: "1.5px dashed var(--ink-3)",
            padding: 40,
            textAlign: "center",
          }}
        >
          <div
            className="display"
            style={{ fontSize: 26, fontStyle: "italic", marginBottom: 8 }}
          >
            No sessions yet
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-3)", maxWidth: 480, margin: "0 auto 16px" }}>
            Picker organises generated images by session. Head to Create, bind a
            generation to a session, and come back here to start judging.
          </div>
          <button className="pkbtn sm" onClick={() => navigate("/create")}>
            ＋ Go to Create
          </button>
        </div>
      )}

      <div
        style={{
          marginTop: 40,
          display: "flex",
          justifyContent: "space-between",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--ink-3)",
          paddingTop: 16,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <span>← click any session to enter the picker</span>
        <span>Esc returns here from any session</span>
      </div>
    </div>
  );
}

function StatCol({ label, value, sub, accent, dot, testid }) {
  return (
    <div
      data-testid={testid}
      style={{
        padding: "14px 16px",
        borderLeft: "1px solid var(--ink)",
        borderRight: 0,
        background: accent ? "var(--banana-soft)" : "var(--paper-soft)",
      }}
    >
      <div
        className="caps mono"
        style={{ fontSize: 9, color: "var(--ink-3)", display: "flex", gap: 6, alignItems: "center" }}
      >
        {dot && <span className={`state-dot ${dot}`} />} {label}
      </div>
      <div
        className="tnum"
        style={{ fontSize: 32, fontWeight: 700, lineHeight: 1, marginTop: 8 }}
      >
        {value}
      </div>
      <div
        className="mono"
        style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 6 }}
      >
        {sub}
      </div>
    </div>
  );
}

function DeckSessionCard({ session, idx, onOpen }) {
  const stats = session.stats || {};
  const total = session.image_count || 0;
  const judged = stats.final + stats.picked + stats.discarded;
  const pct = total > 0 ? Math.round((judged / total) * 100) : 0;
  const inflight =
    (session.in_flight?.queued || 0) +
    (session.in_flight?.running || 0) +
    (session.in_flight?.failed || 0);

  const ready =
    session.picker_state === "judging" &&
    (stats.unjudged || 0) === 0 &&
    (stats.deferred || 0) === 0 &&
    !!session.final_image_id &&
    total > 0;

  return (
    <button
      className={`deck-card${session.picker_state === "finalized" ? " finalized" : ""}${ready ? " ready" : ""}`}
      onClick={onOpen}
      data-testid={`deck-card-${session.id}`}
    >
      <div
        style={{
          aspectRatio: "16 / 10",
          background: "var(--paper-3)",
          position: "relative",
          borderBottom: "1px solid var(--ink)",
          overflow: "hidden",
        }}
      >
        {session.final_thumb_url ? (
          <PickerImage
            imageId={session.final_image_id}
            src={session.final_thumb_url}
            variant="thumb"
            priority={70}
            alt={session.name}
          />
        ) : (
          <div
            className="hatch"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--ink-3)",
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 14,
            }}
          >
            {session.picker_state === "not_started" ? "not started" : "no final yet"}
          </div>
        )}
        <span
          style={{
            position: "absolute",
            top: 8,
            left: 8,
            background: "var(--ink)",
            color: "var(--paper)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            padding: "2px 6px",
            letterSpacing: "0.1em",
          }}
        >
          {String(idx + 1).padStart(2, "0")}
        </span>
        {session.picker_state === "finalized" && (
          <span
            className="pkchip banana"
            style={{ position: "absolute", top: 8, right: 8 }}
          >
            ★ Final
          </span>
        )}
        {ready && (
          <span
            className="pkchip ok"
            style={{ position: "absolute", top: 8, right: 8 }}
          >
            ready
          </span>
        )}
      </div>
      <div style={{ padding: "10px 12px 12px" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            marginBottom: 4,
          }}
        >
          <span style={{ fontSize: 16, fontWeight: 700 }}>{session.name}</span>
          <span
            className="mono tnum"
            style={{ fontSize: 11, color: "var(--ink-3)" }}
          >
            {total} img{total === 1 ? "" : "s"}
          </span>
        </div>
        <div
          className="display"
          style={{
            fontStyle: "italic",
            fontSize: 12,
            color: "var(--ink-2)",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            minHeight: 28,
          }}
        >
          {session.last_prompt || "—"}
        </div>
        <div className="prog-seg" style={{ marginTop: 8 }}>
          <span className="seg-final" style={{ width: pctStr(stats.final, total) }} />
          <span className="seg-picked" style={{ width: pctStr(stats.picked, total) }} />
          <span className="seg-discarded" style={{ width: pctStr(stats.discarded, total) }} />
          <span className="seg-deferred" style={{ width: pctStr(stats.deferred, total) }} />
          <span className="seg-unjudged" style={{ flex: 1 }} />
        </div>
        <div
          className="mono"
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: 8,
            fontSize: 10,
            color: "var(--ink-3)",
          }}
        >
          <span>
            {pct}% judged
            {session.picker_state === "finalized" && " · ★"}
          </span>
          <span>
            {inflight > 0 ? `${inflight} in flight` : session.picker_state.replace(/_/g, " ")}
          </span>
        </div>
      </div>
    </button>
  );
}

function pctStr(num, total) {
  if (!total) return "0%";
  return `${(num / total) * 100}%`;
}

// ---------------------------------------------------------------------
// Board 02 — Session view (judging)
// + 03 drawer + 04 modal + 04b finalized + 04a empty + 04c finalize confirm
// ---------------------------------------------------------------------

function SessionView({ sessionId }) {
  const navigate = useNavigate();
  const ss = picker.usePickerSession();
  const ov = picker.usePickerOverview();
  const data = ss.data;
  const session = data?.session;
  const images = data?.images || [];
  const jobs = data?.jobs || [];

  const finalizedReadOnly = session?.picker_state === "finalized";

  // Cursor index — derived from session.cursor_image_id but the user can
  // also move it locally without persisting yet.
  const [cursorIdx, setCursorIdx] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [compareMode, setCompareMode] = useState("vs-final");
  const [confirmFinalSwap, setConfirmFinalSwap] = useState(null); // { fromImageId, toImageId } | null
  const [toast, setToast] = useState(null);
  // Right-rail collapsed state — persisted across sessions so power
  // users keep the extra real estate they prefer.
  const [metaCollapsed, setMetaCollapsedState] = useState(() => {
    try {
      return localStorage.getItem("txt2img_picker_rail") === "rail";
    } catch {
      return false;
    }
  });
  const setMetaCollapsed = useCallback((next) => {
    setMetaCollapsedState(next);
    try {
      localStorage.setItem(
        "txt2img_picker_rail",
        next ? "rail" : "expanded"
      );
    } catch {
      /* ignore */
    }
  }, []);

  // Initialize cursor from session payload on first load.
  const cursorInited = useRef(false);
  useEffect(() => {
    if (!session) return;
    if (cursorInited.current) return;
    if (images.length === 0) return;
    const cursorId = session.cursor_image_id;
    let idx = 0;
    if (cursorId) {
      idx = images.findIndex((im) => im.image_id === cursorId);
      if (idx < 0) idx = 0;
    } else {
      // Default: first unjudged.
      const unj = images.findIndex((im) => im.pick_state === "unjudged");
      idx = unj >= 0 ? unj : 0;
    }
    setCursorIdx(idx);
    cursorInited.current = true;
  }, [session, images]);

  // Reset cursor-init flag when the session changes.
  useEffect(() => {
    cursorInited.current = false;
  }, [sessionId]);

  // Persist cursor changes via store.
  useEffect(() => {
    const cur = images[cursorIdx];
    if (cur && session) {
      picker.updateCursor(cur.image_id);
    }
  }, [cursorIdx, images, session]);

  const cur = images[cursorIdx] || null;
  const finalImage = useMemo(
    () =>
      session?.final_image_id
        ? images.find((im) => im.image_id === session.final_image_id) || null
        : null,
    [images, session]
  );

  const stats = useMemo(() => picker.computeSessionStats(images), [images]);
  const ready = picker.computeReadyToFinalize(session, images);

  const findNextUnjudged = useCallback(
    (fromIdx) => {
      const n = images.length;
      if (n === 0) return fromIdx;
      for (let k = 1; k <= n; k++) {
        const i = (fromIdx + k) % n;
        if (images[i].pick_state === "unjudged") return i;
      }
      // No unjudged left — first deferred.
      const def = images.findIndex((im) => im.pick_state === "deferred");
      if (def >= 0) return def;
      return fromIdx;
    },
    [images]
  );

  const judgeAndAdvance = useCallback(
    (state) => {
      if (!cur) return;
      if (finalizedReadOnly) return;
      if (state === "final") {
        // If a final already exists and it's a different image, confirm
        // the swap; otherwise just set.
        if (
          session?.final_image_id &&
          session.final_image_id !== cur.image_id
        ) {
          setConfirmFinalSwap({
            from: session.final_image_id,
            to: cur.image_id,
          });
          return;
        }
      }
      picker.judgeImage(cur.image_id, state);
      // Auto-advance.
      setCursorIdx((idx) => {
        const next = findNextUnjudged(idx);
        if (next === idx) {
          setToast("This session is fully judged. Press Enter to finalize.");
          setTimeout(() => setToast(null), 2400);
        }
        return next;
      });
    },
    [cur, session, finalizedReadOnly, findNextUnjudged]
  );

  const undo = useCallback(() => {
    const entry = picker.undoLast();
    if (entry) {
      const idx = images.findIndex((im) => im.image_id === entry.image_id);
      if (idx >= 0) setCursorIdx(idx);
    }
  }, [images]);

  const handleFinalize = useCallback(async () => {
    try {
      await picker.finalizeCurrentSession();
      setToast("Session finalized.");
      setTimeout(() => {
        setToast(null);
        navigate("/picker");
      }, 1200);
    } catch (err) {
      setToast(err?.message || "Could not finalize.");
      setTimeout(() => setToast(null), 2400);
    }
  }, [navigate]);

  // Keyboard
  useEffect(() => {
    const onKey = (e) => {
      const tag = e.target?.tagName;
      const editable = e.target?.isContentEditable;
      if (tag === "INPUT" || tag === "TEXTAREA" || editable) return;
      if (confirmFinalSwap) {
        if (e.key === "Escape") setConfirmFinalSwap(null);
        else if (e.key === "Enter") {
          picker.judgeImage(confirmFinalSwap.to, "final");
          setConfirmFinalSwap(null);
          setCursorIdx((idx) => findNextUnjudged(idx));
        }
        return;
      }
      if (e.key === "Escape") {
        if (drawerOpen) {
          setDrawerOpen(false);
          return;
        }
        navigate("/picker");
        return;
      }
      if (e.key === "ArrowLeft" || e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCursorIdx((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "ArrowRight" || e.key.toLowerCase() === "j") {
        e.preventDefault();
        const next = cur && cur.pick_state === "unjudged"
          ? // PRD §5.1: → on unjudged is equivalent to Space (don't skip)
            (() => {
              if (!finalizedReadOnly) {
                judgeAndAdvance("deferred");
                return cursorIdx;
              }
              return Math.min(images.length - 1, cursorIdx + 1);
            })()
          : Math.min(images.length - 1, cursorIdx + 1);
        setCursorIdx(next);
        return;
      }
      if (e.key === "Home") {
        setCursorIdx(0);
        return;
      }
      if (e.key === "End") {
        setCursorIdx(Math.max(0, images.length - 1));
        return;
      }
      if (e.key === "s" || e.key === "S") {
        setDrawerOpen((v) => !v);
        return;
      }
      if (e.key === "u" || e.key === "U") {
        undo();
        return;
      }
      if (finalizedReadOnly) return;
      if (e.key === "p" || e.key === "P") judgeAndAdvance("picked");
      else if (e.key === "x" || e.key === "X") judgeAndAdvance("discarded");
      else if (e.key === "f" || e.key === "F") judgeAndAdvance("final");
      else if (e.key === " ") {
        e.preventDefault();
        judgeAndAdvance("deferred");
      } else if (e.key === "Enter" && ready) {
        void handleFinalize();
      } else if (/^[1-9]$/.test(e.key)) {
        const n = Number(e.key) - 1;
        const sList = ov.data?.sessions || [];
        if (sList[n]) {
          navigate(`/picker?session_id=${encodeURIComponent(sList[n].id)}`);
          setDrawerOpen(false);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    cur,
    cursorIdx,
    images,
    drawerOpen,
    confirmFinalSwap,
    finalizedReadOnly,
    judgeAndAdvance,
    findNextUnjudged,
    navigate,
    ov.data,
    ready,
    handleFinalize,
    undo,
  ]);

  // Show "session deleted" overlay if SSE marked it.
  if (data?._deleted) {
    return (
      <div
        className="picker-root"
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 32,
        }}
      >
        <div
          className="hatch"
          style={{
            border: "1.5px dashed var(--bad)",
            padding: 32,
            textAlign: "center",
            background: "var(--paper-soft)",
          }}
        >
          <div className="display" style={{ fontSize: 22, marginBottom: 8 }}>
            This session was deleted in another tab.
          </div>
          <button
            className="pkbtn sm"
            onClick={() => navigate("/picker")}
          >
            ← Back to overview
          </button>
        </div>
      </div>
    );
  }

  // Loading skeleton
  if (ss.loading || !data) {
    return (
      <div
        className="picker-root"
        style={{ height: "100%", padding: 32 }}
        data-testid="picker-loading"
      >
        <div style={{ color: "var(--ink-3)" }}>Loading session…</div>
      </div>
    );
  }

  const totalInflight = jobs.filter((j) =>
    ["QUEUED", "RUNNING", "FAILED"].includes(j.status)
  ).length;

  // Empty session — board 04a
  const succeededImages = images.filter((im) => true); // already SUCCEEDED-only from API
  const isEmpty = succeededImages.length === 0;

  return (
    <div
      className="picker-root"
      style={{
        height: "100%",
        display: "grid",
        gridTemplateColumns: metaCollapsed ? "1fr 38px" : "1fr 280px",
        transition: "grid-template-columns 200ms cubic-bezier(.2,.8,.2,1)",
        position: "relative",
        overflow: "hidden",
      }}
      data-testid="picker-session-view"
      data-meta-collapsed={metaCollapsed ? "true" : "false"}
    >
      <SessionMain
        session={session}
        jobs={jobs}
        images={images}
        cur={cur}
        finalImage={finalImage}
        cursorIdx={cursorIdx}
        setCursorIdx={setCursorIdx}
        compareMode={compareMode}
        setCompareMode={setCompareMode}
        stats={stats}
        ready={ready}
        finalizedReadOnly={finalizedReadOnly}
        isEmpty={isEmpty}
        onJudge={judgeAndAdvance}
        onUndo={undo}
        onFinalize={handleFinalize}
        onUnfinalize={async () => {
          try {
            await picker.unfinalizeCurrentSession();
          } catch (err) {
            setToast(err?.message || "Could not reopen.");
            setTimeout(() => setToast(null), 2400);
          }
        }}
        onOpenDrawer={() => setDrawerOpen(true)}
        toast={toast}
      />

      {metaCollapsed ? (
        <MetaRailCollapsed
          stats={stats}
          onExpand={() => setMetaCollapsed(false)}
        />
      ) : (
        <MetaRail
          session={session}
          cur={cur}
          stats={stats}
          jobs={jobs}
          finalizedReadOnly={finalizedReadOnly}
          onCollapse={() => setMetaCollapsed(true)}
        />
      )}

      {/* Drawer (board 03) */}
      <SessionsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        activeId={sessionId}
      />

      {/* Finalize confirm modal (board 04c) */}
      {confirmFinalSwap && (
        <FinalSwapModal
          fromId={confirmFinalSwap.from}
          toId={confirmFinalSwap.to}
          images={images}
          onCancel={() => setConfirmFinalSwap(null)}
          onConfirm={() => {
            picker.judgeImage(confirmFinalSwap.to, "final");
            setConfirmFinalSwap(null);
            setCursorIdx((idx) => findNextUnjudged(idx));
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------
// Main column (header + compare + filmstrip + judgment row)
// ---------------------------------------------------------------------

function SessionMain({
  session,
  jobs,
  images,
  cur,
  finalImage,
  cursorIdx,
  setCursorIdx,
  compareMode,
  setCompareMode,
  stats,
  ready,
  finalizedReadOnly,
  isEmpty,
  onJudge,
  onUndo,
  onFinalize,
  onUnfinalize,
  onOpenDrawer,
  toast,
}) {
  const navigate = useNavigate();
  const judged = stats.picked + stats.discarded + stats.final;
  const inflightR = jobs.filter((j) => j.status === "RUNNING").length;
  const inflightQ = jobs.filter((j) => j.status === "QUEUED").length;
  const inflightF = jobs.filter((j) => j.status === "FAILED").length;
  const totalInflight = inflightR + inflightQ + inflightF;

  // Contact sheet — 8 around cursor.
  const sheet = useMemo(() => {
    const start = Math.max(0, cursorIdx - 1);
    return images.slice(start, start + 8);
  }, [images, cursorIdx]);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateRows: "auto 1fr auto auto auto",
        minWidth: 0,
        borderRight: "1px solid var(--ink)",
        position: "relative",
        overflow: "hidden",
        background: finalizedReadOnly ? "var(--banana)" : "transparent",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "12px 22px",
          borderBottom: "1px solid var(--ink)",
          background: finalizedReadOnly ? "var(--banana)" : "var(--paper-soft)",
          display: "flex",
          gap: 12,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <button
          className="pkbtn sm"
          onClick={onOpenDrawer}
          disabled={finalizedReadOnly && false /* still allowed */}
          title="Sessions (S)"
          data-testid="picker-open-drawer"
        >
          ☰ Sessions <span className="pkkbd" style={{ marginLeft: 4 }}>S</span>
        </button>
        <span className="rule-v" />
        <span className="caps mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Picker
        </span>
        <span
          className="display"
          style={{ fontSize: 22, fontStyle: "italic", fontWeight: 500 }}
          data-testid="picker-session-name"
        >
          {session.name}
        </span>
        {finalizedReadOnly ? (
          <span className="pkchip solid" data-testid="picker-state-chip">
            ★ FINALIZED · LOCKED
          </span>
        ) : ready ? (
          <span className="pkchip ok" data-testid="picker-state-chip">
            READY · F to finalize
          </span>
        ) : (
          <span className="pkchip" data-testid="picker-state-chip">
            <span className="state-dot final" /> {session.picker_state.replace(/_/g, " ")}
          </span>
        )}
        <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
          {judged}/{stats.total} judged
          {totalInflight > 0 && ` · ${totalInflight} in flight`}
        </span>
        <div style={{ flex: 1 }} />
        {!finalizedReadOnly && !isEmpty && (
          <div style={{ display: "flex", border: "1px solid var(--ink)" }}>
            <button
              className="pkbtn sm ghost"
              style={{
                border: "none",
                background:
                  compareMode === "vs-final" ? "var(--ink)" : "transparent",
                color: compareMode === "vs-final" ? "var(--paper)" : "var(--ink)",
              }}
              onClick={() => setCompareMode("vs-final")}
            >
              vs FINAL
            </button>
            <button
              className="pkbtn sm ghost"
              style={{
                border: "none",
                borderLeft: "1px solid var(--ink)",
                background:
                  compareMode === "pair" ? "var(--ink)" : "transparent",
                color: compareMode === "pair" ? "var(--paper)" : "var(--ink)",
              }}
              onClick={() => setCompareMode("pair")}
            >
              Pair
            </button>
          </div>
        )}
        <button
          className="pkbtn sm"
          disabled={finalizedReadOnly || isEmpty}
          onClick={() => window.dispatchEvent(new CustomEvent("picker:fullscreen"))}
        >
          ⛶ Fullscreen
        </button>
      </div>

      {/* Compare surface (or empty / locked variant) */}
      {finalizedReadOnly ? (
        <FinalizedSurface
          finalImage={finalImage}
          stats={stats}
          session={session}
          onUnfinalize={onUnfinalize}
        />
      ) : isEmpty ? (
        <EmptySurface jobs={jobs} session={session} />
      ) : (
        <CompareSurface
          mode={compareMode}
          finalImage={finalImage}
          cur={cur}
          prev={images[Math.max(0, cursorIdx - 1)]}
          cursorIdx={cursorIdx}
          total={images.length}
        />
      )}

      {/* Contact sheet */}
      {!finalizedReadOnly && !isEmpty && (
        <div style={{ padding: "0 22px 14px", background: "var(--paper)" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              marginBottom: 8,
            }}
          >
            <span className="caps mono" style={{ fontSize: 10 }}>
              Up next · contact sheet
            </span>
            <span
              className="mono tnum"
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
              const realIdx = images.findIndex(
                (x) => x.image_id === im.image_id
              );
              const isCurrent = realIdx === cursorIdx;
              return (
                <button
                  key={im.image_id}
                  onClick={() => setCursorIdx(realIdx)}
                  data-testid={`picker-thumb-${im.image_id}`}
                  style={{
                    all: "unset",
                    cursor: "pointer",
                    aspectRatio: "1 / 1",
                    border: isCurrent
                      ? "2px solid var(--banana-deep)"
                      : "1px solid var(--ink)",
                    boxShadow: isCurrent ? "3px 3px 0 var(--ink)" : "none",
                    position: "relative",
                    overflow: "hidden",
                  }}
                >
                  <PickerImage
                    imageId={im.image_id}
                    src={im.thumb_url}
                    variant="thumb"
                    priority={70}
                    alt={`#${realIdx + 1}`}
                    style={{ position: "absolute", inset: 0 }}
                  />
                  <span
                    style={{
                      position: "absolute",
                      top: 4,
                      left: 4,
                      width: 8,
                      height: 8,
                      zIndex: 2,
                    }}
                    className={`state-dot ${im.pick_state}`}
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
                      zIndex: 2,
                    }}
                  >
                    #{realIdx + 1}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Filmstrip */}
      {!isEmpty && (
        <div
          style={{
            borderTop: "1px solid var(--ink)",
            background: finalizedReadOnly ? "var(--banana-soft)" : "var(--paper-soft)",
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
            <span className="caps mono" style={{ fontSize: 10 }}>
              Filmstrip
            </span>
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              click to jump
            </span>
          </div>
          <Filmstrip
            images={images}
            cursorIdx={cursorIdx}
            onPick={setCursorIdx}
            inflightR={inflightR}
            inflightQ={inflightQ}
            inflightF={inflightF}
          />
        </div>
      )}

      {/* Judgment row */}
      <div
        style={{
          borderTop: "1px solid var(--ink)",
          padding: "12px 22px",
          background: finalizedReadOnly ? "var(--banana-soft)" : "var(--paper-soft)",
          display: "flex",
          gap: 12,
          alignItems: "center",
          opacity: finalizedReadOnly || isEmpty ? 0.4 : 1,
          pointerEvents: finalizedReadOnly || isEmpty ? "none" : "auto",
        }}
        data-testid="picker-judgment-row"
      >
        <button
          className="judge-btn"
          data-kind="picked"
          style={{ minWidth: 100 }}
          onClick={() => onJudge("picked")}
          data-testid="picker-judge-pick"
        >
          <div className="glyph">✓</div>
          <div className="lbl">Pick</div>
          <div className="key">P</div>
        </button>
        <button
          className="judge-btn"
          data-kind="discarded"
          style={{ minWidth: 100 }}
          onClick={() => onJudge("discarded")}
          data-testid="picker-judge-discard"
        >
          <div className="glyph">✕</div>
          <div className="lbl">Discard</div>
          <div className="key">X</div>
        </button>
        <button
          className="judge-btn"
          style={{ minWidth: 100 }}
          onClick={() => onJudge("deferred")}
          data-testid="picker-judge-defer"
        >
          <div className="glyph">↺</div>
          <div className="lbl">Defer</div>
          <div className="key">SPACE</div>
        </button>
        <span className="rule-v" />
        <button
          className="judge-btn"
          data-kind="final"
          style={{ minWidth: 140 }}
          onClick={() => onJudge("final")}
          data-testid="picker-judge-final"
        >
          <div className="glyph">★</div>
          <div className="lbl">Set as FINAL</div>
          <div className="key">F</div>
        </button>
        <div style={{ flex: 1 }} />
        {ready && (
          <button
            className="pkbtn sm primary shadowed"
            onClick={onFinalize}
            data-testid="picker-finalize-btn"
          >
            ★ Finalize <span className="pkkbd">↵</span>
          </button>
        )}
        <button
          className="pkbtn sm ghost"
          onClick={onUndo}
          data-testid="picker-undo-btn"
        >
          ↶ Undo · <span className="pkkbd">U</span>
        </button>
      </div>

      {toast && (
        <div
          style={{
            position: "absolute",
            bottom: 16,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--ink)",
            color: "var(--paper)",
            padding: "8px 14px",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            border: "1px solid var(--ink)",
            zIndex: 50,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------
// Compare panes
// ---------------------------------------------------------------------

function ComparePane({ label, image, sub, tone, big }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        minWidth: 0,
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
          className="caps mono"
          style={{
            fontSize: 10,
            background: tone === "banana" ? "var(--banana)" : "var(--ink)",
            color: tone === "banana" ? "var(--ink)" : "var(--paper)",
            padding: "3px 8px",
          }}
        >
          {label}
        </span>
        <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
          {sub}
        </span>
      </div>
      <div
        className={`img-pane ${big && tone === "banana" ? "current" : ""}`}
        style={{
          flex: 1,
          minHeight: 0,
          aspectRatio: image
            ? `${image.width} / ${image.height}`
            : "1 / 1",
          opacity: image ? 1 : 0.5,
          alignSelf: "center",
          maxWidth: "100%",
        }}
      >
        {image ? (
          <>
            <PickerImage
              imageId={image.image_id}
              src={image.download_url}
              variant="full"
              priority={tone === "banana" ? 100 : 90}
              alt={image.image_id}
              style={{
                position: "absolute",
                inset: 0,
                objectFit: "contain",
                background: "var(--paper-3)",
              }}
            />
            <div
              style={{
                position: "absolute",
                top: 8,
                right: 8,
                display: "flex",
                gap: 4,
              }}
            >
              <span
                className="pkchip"
                style={{ background: "var(--paper-soft)", fontSize: 10 }}
              >
                {image.width}×{image.height}
              </span>
            </div>
          </>
        ) : (
          <div
            className="hatch"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--ink-3)",
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 18,
            }}
          >
            no image
          </div>
        )}
      </div>
    </div>
  );
}

function CompareSurface({ mode, finalImage, cur, prev, cursorIdx, total }) {
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
      data-testid="picker-compare"
    >
      <ComparePane
        label={mode === "vs-final" ? "CURRENT FINAL" : "← Previous"}
        image={mode === "vs-final" ? finalImage : prev}
        sub={mode === "vs-final" ? "the slide will use this" : "press ← to step"}
        tone="muted"
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
        image={cur}
        sub={cur?.pick_state === "unjudged" ? "awaiting judgment" : cur?.pick_state}
        tone="banana"
        big
      />
    </div>
  );
}

function EmptySurface({ jobs, session }) {
  return (
    <div
      style={{
        padding: 22,
        background: "var(--paper)",
        minHeight: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
      data-testid="picker-empty-surface"
    >
      <div
        className="hatch"
        style={{
          border: "1.5px dashed var(--ink-3)",
          padding: 32,
          maxWidth: 720,
          textAlign: "center",
        }}
      >
        <div
          className="display"
          style={{ fontSize: 36, fontStyle: "italic", marginBottom: 10 }}
        >
          {jobs.length === 0 ? "No images, no jobs" : "No images yet"}
        </div>
        <div
          style={{
            fontSize: 13,
            color: "var(--ink-3)",
            maxWidth: 480,
            margin: "0 auto 18px",
          }}
        >
          {jobs.length === 0
            ? `This session has nothing yet. Bind a generation to "${session.name}" from the Create page.`
            : "Jobs are running for this session. They'll land here as they complete."}
        </div>
        <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
          {jobs
            .filter((j) => ["RUNNING", "QUEUED", "FAILED"].includes(j.status))
            .slice(0, 3)
            .map((j, i) => (
              <div
                key={j.hash_id}
                className={`if-card ${j.status === "RUNNING" ? "running" : j.status === "FAILED" ? "failed" : "queued"}`}
                style={{ width: 180, gap: 6 }}
              >
                <div className="head">
                  <span>{j.status}</span>
                  <span>—</span>
                </div>
                <div className="prog-seg" style={{ height: 4 }}>
                  <span
                    className="seg-final"
                    style={{
                      width: j.status === "RUNNING" ? "40%" : "0%",
                      background:
                        j.status === "FAILED" ? "var(--bad)" : "var(--banana)",
                    }}
                  />
                  <span className="seg-unjudged" style={{ flex: 1 }} />
                </div>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

function FinalizedSurface({ finalImage, stats, session, onUnfinalize }) {
  return (
    <div
      style={{
        padding: 22,
        background: "var(--paper)",
        minHeight: 0,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 22,
        alignItems: "stretch",
      }}
      data-testid="picker-finalized-surface"
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span
          className="caps mono pkchip banana"
          style={{ alignSelf: "flex-start", fontSize: 10 }}
        >
          ★ FINAL
        </span>
        <div
          className="img-pane current"
          style={{
            flex: 1,
            minHeight: 0,
            aspectRatio: finalImage
              ? `${finalImage.width} / ${finalImage.height}`
              : "1 / 1",
          }}
        >
          {finalImage && (
            <PickerImage
              imageId={finalImage.image_id}
              src={finalImage.download_url}
              variant="full"
              priority={100}
              style={{ position: "absolute", inset: 0, objectFit: "contain" }}
            />
          )}
          <span className="img-tag banana">★ FINAL · IN SLIDE</span>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div
          style={{
            border: "1px solid var(--ink)",
            background: "var(--paper-soft)",
            padding: 14,
          }}
        >
          <span
            className="caps mono"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            Outcome
          </span>
          <div
            style={{
              marginTop: 8,
              display: "grid",
              gridTemplateColumns: "auto 1fr auto",
              rowGap: 6,
              columnGap: 8,
              alignItems: "center",
            }}
          >
            <span className="state-dot final" />
            <span style={{ fontSize: 13 }}>Final</span>
            <span className="mono tnum" style={{ fontWeight: 700 }}>
              {stats.final}
            </span>
            <span className="state-dot picked" />
            <span style={{ fontSize: 13 }}>Picked</span>
            <span className="mono tnum" style={{ fontWeight: 700 }}>
              {stats.picked}
            </span>
            <span className="state-dot discarded" />
            <span style={{ fontSize: 13 }}>Discarded</span>
            <span className="mono tnum" style={{ fontWeight: 700 }}>
              {stats.discarded}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className="pkbtn sm"
            onClick={onUnfinalize}
            data-testid="picker-reopen-btn"
          >
            ↺ Reopen for judging
          </button>
          <button className="pkbtn sm" disabled>
            ⤓ Export
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Filmstrip with hover preview
// ---------------------------------------------------------------------

function Filmstrip({
  images,
  cursorIdx,
  onPick,
  inflightR,
  inflightQ,
  inflightF,
  height = 22,
}) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);
  const [mouseX, setMouseX] = useState(0);

  return (
    <div
      ref={ref}
      style={{ position: "relative", padding: "8px 22px 6px" }}
      onMouseMove={(e) => {
        const rect = ref.current?.getBoundingClientRect();
        if (rect) setMouseX(e.clientX - rect.left);
      }}
      data-testid="picker-filmstrip"
    >
      {hover !== null && images[hover] && (
        <div
          style={{
            position: "absolute",
            bottom: "calc(100% - 4px)",
            left: Math.max(
              60,
              Math.min((ref.current?.offsetWidth || 0) - 60, mouseX)
            ),
            transform: "translateX(-50%)",
            width: 120,
            height: 120,
            border: "1.5px solid var(--ink)",
            boxShadow: "0 4px 0 var(--ink)",
            zIndex: 30,
            pointerEvents: "none",
            background: "var(--paper-3)",
            overflow: "hidden",
          }}
        >
          <PickerImage
            imageId={images[hover].image_id}
            src={images[hover].thumb_url}
            variant="thumb"
            priority={60}
            style={{ position: "absolute", inset: 0 }}
          />
          <span
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              background: "var(--ink)",
              color: "var(--paper)",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              padding: "1px 5px",
            }}
          >
            #{hover + 1} · {images[hover].pick_state}
          </span>
        </div>
      )}
      <div
        style={{
          display: "flex",
          height,
          border: "1px solid var(--ink)",
          background: "var(--paper-3)",
        }}
      >
        {images.map((im, i) => (
          <div
            key={im.image_id}
            className={`fs-cell ${im.pick_state} ${i === cursorIdx ? "current" : ""}`}
            onClick={() => onPick(i)}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            style={{ height }}
            data-testid={`fs-cell-${im.image_id}`}
          />
        ))}
        {Array.from({ length: inflightR }).map((_, i) => (
          <div key={`r${i}`} className="fs-cell running" style={{ height }} />
        ))}
        {Array.from({ length: inflightQ }).map((_, i) => (
          <div key={`q${i}`} className="fs-cell queued" style={{ height }} />
        ))}
        {Array.from({ length: inflightF }).map((_, i) => (
          <div key={`f${i}`} className="fs-cell failed" style={{ height }} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Right meta rail
// ---------------------------------------------------------------------

function MetaRail({ session, cur, stats, jobs, finalizedReadOnly, onCollapse }) {
  const navigate = useNavigate();
  const inflight = jobs.filter((j) =>
    ["QUEUED", "RUNNING", "FAILED"].includes(j.status)
  );
  return (
    <div
      style={{
        background: "var(--paper-2)",
        display: "grid",
        gridTemplateRows: "auto auto auto auto 1fr auto",
        minHeight: 0,
        borderLeft: "1px solid var(--ink)",
      }}
      data-testid="picker-meta-rail"
    >
      {/* Collapse handle — sits at the top of the rail. Mirrors the
          sidebar collapse pattern so the gesture feels familiar. */}
      <div
        style={{
          padding: "8px 12px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid var(--rule)",
          background: "var(--paper-3)",
        }}
      >
        <span
          className="caps mono"
          style={{ fontSize: 9, color: "var(--ink-3)" }}
        >
          Side panel
        </span>
        <button
          onClick={onCollapse}
          title="Hide panel — give compare more room"
          data-testid="picker-meta-collapse"
          style={{
            all: "unset",
            cursor: "pointer",
            padding: "2px 8px",
            border: "1px solid var(--ink)",
            background: "var(--paper-soft)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            fontWeight: 700,
            color: "var(--ink)",
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
          }}
        >
          <span>›</span>
          <span>HIDE</span>
        </button>
      </div>
      <RailSection title="Outcome">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr auto",
            rowGap: 6,
            columnGap: 8,
            alignItems: "center",
          }}
        >
          <span className="state-dot final" />
          <span style={{ fontSize: 12 }}>Final</span>
          <span data-testid="picker-outcome-final" className="mono tnum" style={{ fontSize: 12, fontWeight: 700 }}>
            {stats.final}
          </span>
          <span className="state-dot picked" />
          <span style={{ fontSize: 12 }}>Picked</span>
          <span data-testid="picker-outcome-picked" className="mono tnum" style={{ fontSize: 12, fontWeight: 700 }}>
            {stats.picked}
          </span>
          <span className="state-dot discarded" />
          <span style={{ fontSize: 12 }}>Discarded</span>
          <span data-testid="picker-outcome-discarded" className="mono tnum" style={{ fontSize: 12, fontWeight: 700 }}>
            {stats.discarded}
          </span>
          <span className="state-dot deferred" />
          <span style={{ fontSize: 12 }}>Deferred</span>
          <span data-testid="picker-outcome-deferred" className="mono tnum" style={{ fontSize: 12, fontWeight: 700 }}>
            {stats.deferred}
          </span>
          <span className="state-dot unjudged" />
          <span style={{ fontSize: 12 }}>Unjudged</span>
          <span data-testid="picker-outcome-unjudged" className="mono tnum" style={{ fontSize: 12, fontWeight: 700 }}>
            {stats.unjudged}
          </span>
        </div>
      </RailSection>

      <RailSection title="Prompt">
        <p
          style={{
            margin: 0,
            fontFamily: "var(--font-display)",
            fontSize: 13,
            lineHeight: 1.4,
            fontStyle: "italic",
            color: "var(--ink-2)",
          }}
        >
          “{jobs[0]?.prompt || "—"}”
        </p>
      </RailSection>

      <RailSection title="Image" sub={cur ? `${cur.width}×${cur.height}` : "—"}>
        {cur && (
          <div
            data-testid="picker-image-info"
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
            <span data-testid="picker-image-info-id" style={{ wordBreak: "break-all" }}>{cur.image_id}</span>
            <span style={{ color: "var(--ink-3)" }}>seed</span>
            <span data-testid="picker-image-info-seed">{cur.seed || "—"}</span>
            <span style={{ color: "var(--ink-3)" }}>job</span>
            <span data-testid="picker-image-info-job">
              #{cur.job_idx + 1} · slot {cur.order}
            </span>
            <span style={{ color: "var(--ink-3)" }}>state</span>
            <span
              data-testid="picker-image-info-state"
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
        )}
      </RailSection>

      <RailSection
        title="In flight"
        sub={`${inflight.length} active`}
        grow
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {inflight.length === 0 && (
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
          {inflight.map((j) => (
            <div
              key={j.hash_id}
              className={`if-card ${j.status === "RUNNING" ? "running" : j.status === "FAILED" ? "failed" : "queued"}`}
            >
              <div className="head">
                <span>{j.status}</span>
                <span>—</span>
              </div>
              <div className="prog-seg" style={{ height: 4 }}>
                <span
                  className="seg-final"
                  style={{
                    width: j.status === "RUNNING" ? "60%" : "0%",
                    background:
                      j.status === "FAILED" ? "var(--bad)" : "var(--banana)",
                  }}
                />
                <span className="seg-unjudged" style={{ flex: 1 }} />
              </div>
              <div
                style={{
                  fontSize: 10,
                  color: "var(--ink-3)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {j.prompt?.slice(0, 60)}
                {j.prompt && j.prompt.length > 60 ? "…" : ""}
              </div>
            </div>
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
          className="pkbtn sm"
          disabled={!cur}
          onClick={() =>
            cur && navigate(`/create?ref_image=${encodeURIComponent(cur.image_id)}`)
          }
        >
          ↧ Use as ref
        </button>
        <button
          className="pkbtn sm"
          disabled={!cur || finalizedReadOnly}
          onClick={async () => {
            if (!cur) return;
            try {
              await picker.varySeedFor(cur.image_id);
            } catch (err) {
              window.alert(err?.message || "Could not vary seed.");
            }
          }}
          data-testid="picker-vary-btn"
        >
          ⌖ Vary seed
        </button>
        <button
          className="pkbtn sm"
          onClick={() =>
            session && navigate(`/create?session_id=${encodeURIComponent(session.id)}`)
          }
        >
          ＋ Generate
        </button>
      </div>
    </div>
  );
}

// Slim rail shown when the side panel is collapsed. Keeps the user
// oriented (5 mini state dots with counts) and offers a re-expand
// button. Lives in a 38px-wide column so the compare surface gains
// 240+ pixels of breathing room.
function MetaRailCollapsed({ stats, onExpand }) {
  const total = stats.total || 0;
  const judged = stats.final + stats.picked + stats.discarded;
  const pct = total > 0 ? Math.round((judged / total) * 100) : 0;
  return (
    <div
      style={{
        background: "var(--paper-2)",
        borderLeft: "1px solid var(--ink)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 0",
        minHeight: 0,
      }}
      data-testid="picker-meta-rail-collapsed"
    >
      <button
        onClick={onExpand}
        title="Show panel"
        data-testid="picker-meta-expand"
        style={{
          all: "unset",
          cursor: "pointer",
          width: 26,
          height: 26,
          border: "1px solid var(--ink)",
          background: "var(--paper-soft)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          fontWeight: 700,
          color: "var(--ink)",
        }}
      >
        ‹
      </button>

      {/* Vertical mini outcome stack — keeps the user aware without
          stealing horizontal real estate. Each row is dot + tabular
          count, rotated nowhere; we rely on a single-column layout. */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 8,
          flex: 1,
          justifyContent: "center",
        }}
      >
        <CollapsedStat dot="final" value={stats.final} />
        <CollapsedStat dot="picked" value={stats.picked} />
        <CollapsedStat dot="discarded" value={stats.discarded} />
        <CollapsedStat dot="deferred" value={stats.deferred} />
        <CollapsedStat dot="unjudged" value={stats.unjudged} />
      </div>

      {/* Bottom: percent judged in mono, written sideways for visual
          interest in the slim column. */}
      <div
        className="mono tnum"
        style={{
          writingMode: "vertical-rl",
          transform: "rotate(180deg)",
          fontSize: 10,
          color: "var(--ink-3)",
          letterSpacing: "0.1em",
          padding: "8px 0",
        }}
      >
        {pct}% judged
      </div>
    </div>
  );
}

function CollapsedStat({ dot, value }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 2,
      }}
    >
      <span className={`state-dot ${dot}`} style={{ width: 10, height: 10 }} />
      <span
        className="mono tnum"
        style={{
          fontSize: 10,
          fontWeight: 700,
          color: "var(--ink-2)",
          minWidth: 14,
          textAlign: "center",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function RailSection({ title, sub, children, grow }) {
  return (
    <div
      style={{
        padding: "12px 14px",
        borderTop: "1px solid var(--rule-2)",
        ...(grow ? { minHeight: 0, overflowY: "auto" } : {}),
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 8,
        }}
      >
        <span className="caps mono" style={{ fontSize: 10 }}>
          {title}
        </span>
        {sub && (
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            {sub}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------
// Sessions drawer (board 03)
// ---------------------------------------------------------------------

function SessionsDrawer({ open, onClose, activeId }) {
  const navigate = useNavigate();
  const ov = picker.usePickerOverview();
  const sessions = ov.data?.sessions || [];
  const finalized = sessions.filter((s) => s.picker_state === "finalized").length;

  const onCreate = useCallback(async () => {
    const name = window.prompt("New session name:");
    if (!name) return;
    try {
      const created = await createSession(name.trim());
      void picker.syncOverview();
      navigate(`/picker?session_id=${encodeURIComponent(created.id)}`);
      onClose();
    } catch (err) {
      window.alert(err?.message || "Could not create session.");
    }
  }, [navigate, onClose]);

  return (
    <>
      {open && (
        <div
          onClick={onClose}
          style={{
            position: "absolute",
            inset: 0,
            background: "#19171444",
            zIndex: 20,
            animation: "pkFadeIn 140ms ease",
          }}
        />
      )}
      <div
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          right: 0,
          width: 320,
          transform: open ? "translateX(0)" : "translateX(100%)",
          transition: "transform 200ms cubic-bezier(.2,.8,.2,1)",
          background: "var(--paper-2)",
          borderLeft: "1px solid var(--ink)",
          boxShadow: "-12px 0 32px -16px #19171455",
          display: "grid",
          gridTemplateRows: "auto 1fr auto",
          zIndex: 21,
        }}
        data-testid="picker-drawer"
      >
        <div
          style={{
            padding: "12px 14px",
            borderBottom: "1px solid var(--ink)",
            background: "var(--paper-soft)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <span
              className="caps mono"
              style={{ fontSize: 10, color: "var(--ink-3)" }}
            >
              Picker
            </span>
            <div
              className="display"
              style={{ fontStyle: "italic", fontSize: 18, marginTop: 2 }}
            >
              Sessions
            </div>
          </div>
          <button
            className="pkbtn sm ghost"
            onClick={onClose}
            title="Close (S or Esc)"
          >
            ✕ <span className="pkkbd" style={{ marginLeft: 4 }}>S</span>
          </button>
        </div>
        <div style={{ overflowY: "auto" }} data-testid="picker-drawer-list">
          {sessions.map((s) => (
            <DrawerRow
              key={s.id}
              session={s}
              active={s.id === activeId}
              onClick={() => {
                navigate(`/picker?session_id=${encodeURIComponent(s.id)}`);
                onClose();
              }}
            />
          ))}
        </div>
        <div
          style={{
            padding: "10px 14px",
            borderTop: "1px solid var(--ink)",
            background: "var(--paper-3)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            {finalized}/{sessions.length} finalized
          </span>
          <button className="pkbtn sm" onClick={onCreate}>
            ＋ New session
          </button>
        </div>
      </div>
    </>
  );
}

function DrawerRow({ session, active, onClick }) {
  const stats = session.stats || {};
  const judged = (stats.final || 0) + (stats.picked || 0) + (stats.discarded || 0);
  const total = session.image_count || 0;
  const pct = total > 0 ? Math.round((judged / total) * 100) : 0;
  const if_q = session.in_flight?.queued || 0;
  const if_r = session.in_flight?.running || 0;
  const if_f = session.in_flight?.failed || 0;

  let badge = null;
  if (session.picker_state === "finalized") {
    badge = <span className="badge banana">★ final</span>;
  } else if (
    session.picker_state === "judging" &&
    !stats.unjudged &&
    !stats.deferred &&
    session.final_image_id &&
    total > 0
  ) {
    badge = <span className="badge ok">ready</span>;
  } else if (if_r > 0) {
    badge = <span className="badge running">{if_r}↻</span>;
  } else if (if_q > 0) {
    badge = <span className="badge queued">{if_q}…</span>;
  } else if (if_f > 0) {
    badge = <span className="badge failed">{if_f}✕</span>;
  } else if (session.picker_state === "not_started") {
    badge = <span className="badge">∅</span>;
  }

  return (
    <button
      className="s-row"
      data-active={active ? "true" : "false"}
      onClick={onClick}
      data-testid={`drawer-row-${session.id}`}
    >
      <div style={{ minWidth: 0 }}>
        <div className="name">{session.name}</div>
        <div className="sub">
          {total} imgs ·{" "}
          {session.picker_state === "not_started" ? "—" : `${pct}%`}
        </div>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-end",
          gap: 4,
        }}
      >
        {badge}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------
// Final-swap confirm modal (board 04c)
// ---------------------------------------------------------------------

function FinalSwapModal({ fromId, toId, images, onCancel, onConfirm }) {
  const fromImg = images.find((im) => im.image_id === fromId);
  const toImg = images.find((im) => im.image_id === toId);
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "#19171466",
        zIndex: 50,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        animation: "pkFadeIn 140ms ease",
      }}
      onClick={onCancel}
      data-testid="picker-final-swap-modal"
    >
      <div
        style={{
          width: 580,
          background: "var(--paper-soft)",
          border: "1.5px solid var(--ink)",
          boxShadow: "12px 12px 0 var(--ink)",
          padding: 24,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="display"
          style={{
            fontSize: 24,
            fontStyle: "italic",
            marginBottom: 8,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span style={{ color: "var(--banana-deep)" }}>★</span>
          Replace the FINAL?
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr auto 1fr",
            gap: 12,
            margin: "16px 0",
          }}
        >
          <div>
            <span
              className="caps mono"
              style={{ fontSize: 10, color: "var(--ink-3)" }}
            >
              Currently FINAL
            </span>
            <div
              className="img-pane"
              style={{
                aspectRatio: fromImg
                  ? `${fromImg.width} / ${fromImg.height}`
                  : "1 / 1",
                marginTop: 8,
              }}
            >
              {fromImg && (
                <PickerImage
                  imageId={fromImg.image_id}
                  src={fromImg.thumb_url}
                  variant="thumb"
                  priority={90}
                  style={{ position: "absolute", inset: 0 }}
                />
              )}
              <span className="img-tag muted">→ becomes PICKED</span>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 32,
              color: "var(--ink-3)",
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
            }}
          >
            ↦
          </div>
          <div>
            <span className="caps mono pkchip banana" style={{ fontSize: 10 }}>
              NEW FINAL
            </span>
            <div
              className="img-pane current"
              style={{
                aspectRatio: toImg
                  ? `${toImg.width} / ${toImg.height}`
                  : "1 / 1",
                marginTop: 8,
              }}
            >
              {toImg && (
                <PickerImage
                  imageId={toImg.image_id}
                  src={toImg.thumb_url}
                  variant="thumb"
                  priority={100}
                  style={{ position: "absolute", inset: 0 }}
                />
              )}
              <span className="img-tag banana">★ FINAL</span>
            </div>
          </div>
        </div>
        <div
          style={{ fontSize: 13, color: "var(--ink-2)", marginBottom: 14 }}
        >
          Each session has exactly one FINAL. Confirming will demote the
          current final to picked and use the new image.
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="pkbtn sm" onClick={onCancel}>
            Cancel · <span className="pkkbd">Esc</span>
          </button>
          <button
            className="pkbtn sm primary shadowed"
            onClick={onConfirm}
            data-testid="picker-final-swap-confirm"
          >
            ★ Confirm swap · <span className="pkkbd">↵</span>
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Board 05 — Fullscreen
// ---------------------------------------------------------------------

function FullscreenView({ sessionId, onExit }) {
  const ss = picker.usePickerSession();
  const data = ss.data;
  const session = data?.session;
  const images = data?.images || [];
  const jobs = data?.jobs || [];

  const [cursorIdx, setCursorIdx] = useState(0);
  const cursorInited = useRef(false);
  useEffect(() => {
    if (!session || cursorInited.current || images.length === 0) return;
    const cursorId = session.cursor_image_id;
    let idx = 0;
    if (cursorId) {
      idx = images.findIndex((im) => im.image_id === cursorId);
      if (idx < 0) idx = 0;
    }
    setCursorIdx(idx);
    cursorInited.current = true;
  }, [session, images]);

  // Sync cursor changes back to the picker store so SessionView can
  // resume at the same position after exiting fullscreen. Skip until
  // the initial cursor has been restored from session.cursor_image_id —
  // otherwise the default cursorIdx=0 overwrites the saved value.
  useEffect(() => {
    if (!cursorInited.current) return;
    const cur = images[cursorIdx];
    if (cur && session) picker.updateCursor(cur.image_id);
  }, [cursorIdx, images, session]);

  const cur = images[cursorIdx] || null;
  const finalImage =
    session?.final_image_id
      ? images.find((im) => im.image_id === session.final_image_id) || null
      : null;
  const stats = useMemo(() => picker.computeSessionStats(images), [images]);
  const judged = stats.picked + stats.discarded + stats.final;
  const judgedPct = stats.total
    ? Math.round((judged / stats.total) * 100)
    : 0;

  const judgeAndAdvance = useCallback(
    (state) => {
      if (!cur) return;
      picker.judgeImage(cur.image_id, state);
      setCursorIdx((idx) => {
        const n = images.length;
        for (let k = 1; k <= n; k++) {
          const i = (idx + k) % n;
          if (images[i].pick_state === "unjudged") return i;
        }
        return idx;
      });
    },
    [cur, images]
  );

  useEffect(() => {
    const onKey = (e) => {
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "ArrowLeft")
        setCursorIdx((i) => Math.max(0, i - 1));
      else if (e.key === "ArrowRight")
        setCursorIdx((i) => Math.min(images.length - 1, i + 1));
      else if (e.key === "p" || e.key === "P") judgeAndAdvance("picked");
      else if (e.key === "x" || e.key === "X") judgeAndAdvance("discarded");
      else if (e.key === "f" || e.key === "F") judgeAndAdvance("final");
      else if (e.key === " ") {
        e.preventDefault();
        judgeAndAdvance("deferred");
      } else if (e.key === "u" || e.key === "U") picker.undoLast();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [judgeAndAdvance, images]);

  const inflightR = jobs.filter((j) => j.status === "RUNNING").length;
  const inflightQ = jobs.filter((j) => j.status === "QUEUED").length;
  const inflightF = jobs.filter((j) => j.status === "FAILED").length;

  // Portal to document.body so we escape the parent route-stage
  // which has `will-change: transform` (creating a stacking context
  // that traps `position: fixed`).
  const node = (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "#0e0d0b",
        color: "var(--paper)",
        zIndex: 9999,
        display: "grid",
        // topbar / labels-strip / compare-images / filmstrip
        gridTemplateRows: "auto auto 1fr auto",
      }}
      data-testid="picker-fullscreen"
    >
      <div
        style={{
          padding: "6px 16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: "1px solid #ffffff15",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span
            className="mono caps"
            style={{ fontSize: 10, color: "var(--banana)", letterSpacing: "0.2em" }}
          >
            FULLSCREEN · {session?.name || ""}
          </span>
          <span
            className="mono"
            style={{ fontSize: 11, color: "#ffffff80" }}
          >
            #{cursorIdx + 1} / {images.length} · {judgedPct}%
          </span>
        </div>
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <FsKbd k="P" lbl="pick" />
          <FsKbd k="X" lbl="discard" />
          <FsKbd k="F" lbl="final" />
          <FsKbd k="␣" lbl="defer" />
          <FsKbd k="←→" lbl="nav" />
          <FsKbd k="U" lbl="undo" />
          <span style={{ width: 1, height: 18, background: "#ffffff20" }} />
          <button
            onClick={onExit}
            style={{
              all: "unset",
              cursor: "pointer",
              padding: "4px 10px",
              fontSize: 12,
              color: "var(--paper)",
              border: "1px solid #ffffff40",
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.1em",
            }}
            data-testid="picker-fullscreen-exit"
          >
            EXIT · Esc
          </button>
        </div>
      </div>
      {/* Label strip — sits ABOVE the images so labels never overlay
          the photo. Two-column layout matches the compare grid below. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          padding: "8px 12px 0",
          minHeight: 0,
        }}
      >
        <FsLabelRow
          label="CURRENT FINAL"
          dimensions={finalImage ? `${finalImage.width}×${finalImage.height}` : null}
        />
        <FsLabelRow
          label={`CANDIDATE · #${cursorIdx + 1}`}
          dimensions={cur ? `${cur.width}×${cur.height}` : null}
          state={cur && cur.pick_state !== "unjudged" ? cur.pick_state : null}
          highlight
        />
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          padding: "4px 12px 12px",
          minHeight: 0,
        }}
      >
        <FsPane image={finalImage} />
        <FsPane image={cur} highlight />
      </div>
      <div
        style={{
          background: "#16140f",
          borderTop: "1px solid #ffffff15",
          padding: "6px 16px 8px",
        }}
      >
        <Filmstrip
          images={images}
          cursorIdx={cursorIdx}
          onPick={setCursorIdx}
          inflightR={inflightR}
          inflightQ={inflightQ}
          inflightF={inflightF}
          height={28}
        />
      </div>
    </div>
  );
  return typeof document !== "undefined"
    ? createPortal(node, document.body)
    : node;
}

function FsKbd({ k, lbl }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "#ffffff70",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          minWidth: 18,
          height: 18,
          padding: "0 4px",
          border: "1px solid #ffffff40",
          fontSize: 10,
          fontWeight: 700,
          color: "var(--paper)",
        }}
      >
        {k}
      </span>
      {lbl}
    </span>
  );
}

// Compact label row — sits in the dark band above the images. No
// overlap with the photo. The dimension chip and pick_state badge live
// here so the image surface stays clean.
function FsLabelRow({ label, dimensions, state, highlight }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "4px 2px",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        letterSpacing: "0.2em",
        color: highlight ? "var(--banana)" : "#ffffff80",
      }}
    >
      <span
        style={{
          textTransform: "uppercase",
          fontWeight: 700,
        }}
      >
        {highlight && "▸ "}
        {label}
      </span>
      {state && (
        <span
          style={{
            background: "var(--banana)",
            color: "var(--ink)",
            padding: "2px 7px",
            border: "1px solid var(--banana)",
            fontWeight: 700,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
          }}
        >
          {state}
        </span>
      )}
      <div style={{ flex: 1 }} />
      {dimensions && (
        <span style={{ color: "#ffffff60", fontSize: 10 }}>
          {dimensions}
        </span>
      )}
    </div>
  );
}

function FsPane({ image, highlight }) {
  return (
    <div
      style={{
        // Single full-area pane. The image fills 100% × 100% with
        // object-fit:contain. Border is on the pane edge — never on
        // top of the image content.
        position: "relative",
        minHeight: 0,
        minWidth: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0e0d0b",
        border: highlight
          ? "2px solid var(--banana)"
          : "1px solid #ffffff20",
        boxShadow: highlight
          ? "0 20px 60px -20px #00000099"
          : "0 20px 60px -20px #00000066",
        opacity: image ? 1 : 0.5,
      }}
    >
      {image ? (
        <PickerImage
          imageId={image.image_id}
          src={image.download_url}
          variant="full"
          priority={highlight ? 100 : 90}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "contain",
            display: "block",
          }}
        />
      ) : (
        <div
          style={{
            width: "60%",
            aspectRatio: "1/1",
            border: "1.5px dashed #ffffff20",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#ffffff40",
            fontFamily: "var(--font-display)",
            fontStyle: "italic",
            fontSize: 22,
          }}
        >
          no final yet
        </div>
      )}
    </div>
  );
}
