// SessionsDrawer — slides in from the right edge of the picker page.
// Used to switch sessions without leaving the page (PRD §4.3).

import { useEffect } from "react";

export default function SessionsDrawer({
  open,
  onClose,
  activeId,
  sessions,
  onSelect,
  onNewSession,
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const finalized = sessions.filter((s) => s.picker_state === "finalized").length;

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
        data-testid="picker-sessions-drawer"
        data-open={open ? "true" : "false"}
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
          pointerEvents: open ? "auto" : "none",
        }}
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
              className="caps"
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
            className="btn sm ghost"
            onClick={onClose}
            type="button"
            title="Close (S or Esc)"
          >
            ✕ <span className="kbd" style={{ marginLeft: 4 }}>S</span>
          </button>
        </div>

        <div style={{ overflowY: "auto" }}>
          {sessions.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              active={s.id === activeId}
              onClick={() => onSelect(s.id)}
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
          <button
            className="btn sm"
            onClick={onNewSession}
            type="button"
          >
            ＋ New session
          </button>
        </div>
      </div>
    </>
  );
}

function SessionRow({ session, active, onClick }) {
  const judged = session.judged_count || 0;
  const total = session.image_count || 0;
  const pct = total ? Math.round((judged / total) * 100) : 0;
  const isReady =
    session.picker_state === "judging" &&
    !!session.final_image_id &&
    (session.stats?.unjudged ?? 0) === 0 &&
    (session.stats?.deferred ?? 0) === 0;

  let badge = null;
  if (session.picker_state === "finalized") {
    badge = <span className="badge banana">★ final</span>;
  } else if (isReady) {
    badge = <span className="badge ok">ready</span>;
  } else if ((session.in_flight?.running ?? 0) > 0) {
    badge = <span className="badge running">{session.in_flight.running}↻</span>;
  } else if ((session.in_flight?.queued ?? 0) > 0) {
    badge = <span className="badge queued">{session.in_flight.queued}…</span>;
  } else if ((session.in_flight?.failed ?? 0) > 0) {
    badge = <span className="badge failed">{session.in_flight.failed}✕</span>;
  } else if (session.picker_state === "not_started") {
    badge = <span className="badge">∅</span>;
  }

  const stats = session.stats || {};
  const dotsTotal = Math.min(8, total);
  const dots = Array.from({ length: dotsTotal }).map((_, i) => {
    let cls = "unjudged";
    if (i < (stats.final || 0)) cls = "final";
    else if (i < (stats.final || 0) + (stats.picked || 0)) cls = "picked";
    else if (
      i <
      (stats.final || 0) + (stats.picked || 0) + (stats.discarded || 0)
    )
      cls = "discarded";
    else if (
      i <
      (stats.final || 0) +
        (stats.picked || 0) +
        (stats.discarded || 0) +
        (stats.deferred || 0)
    )
      cls = "deferred";
    return (
      <span
        key={i}
        className={`pk-state-dot ${cls}`}
        style={{ width: 5, height: 5, border: "0.5px solid var(--ink-3)" }}
      />
    );
  });

  return (
    <button
      className="pk-s-row"
      data-active={active ? "true" : "false"}
      onClick={onClick}
      type="button"
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
        <div style={{ display: "flex", gap: 1 }}>{dots}</div>
      </div>
    </button>
  );
}
