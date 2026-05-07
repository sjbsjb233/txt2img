// Deck overview — entry page (PRD §4.1).
// Top band with deck identity, 5-column stat strip, then a 4-col session
// grid with finalize-all CTA.

import { useNavigate } from "react-router-dom";
import AuthorizedImage from "../../components/AuthorizedImage.jsx";

function pad2(n) {
  return String(n).padStart(2, "0");
}

function pct(num, denom) {
  if (!denom) return 0;
  return Math.round((num / denom) * 100);
}

function formatRelative(iso) {
  if (!iso) return "—";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "—";
  const diffMs = Date.now() - dt.getTime();
  if (diffMs < 60_000) return "just now";
  if (diffMs < 3_600_000) return `${Math.round(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.round(diffMs / 3_600_000)}h ago`;
  return dt.toLocaleString();
}

function isReadyToFinalize(s) {
  if (s.picker_state !== "judging") return false;
  if (!s.final_image_id) return false;
  const u = s.stats?.unjudged ?? 0;
  const d = s.stats?.deferred ?? 0;
  return u === 0 && d === 0 && (s.image_count || 0) > 0;
}

export default function DeckOverview({ snapshot, onFinalizeAllReady }) {
  const navigate = useNavigate();
  const sessions = snapshot.overview;

  const totals = sessions.reduce(
    (a, s) => {
      a.images += s.image_count || 0;
      a.judged += s.judged_count || 0;
      a.inflight += (s.in_flight?.queued || 0) + (s.in_flight?.running || 0) + (s.in_flight?.failed || 0);
      return a;
    },
    { images: 0, judged: 0, inflight: 0 }
  );
  const finalized = sessions.filter((s) => s.picker_state === "finalized").length;
  const ready = sessions.filter(isReadyToFinalize).length;
  const judging = sessions.filter(
    (s) => s.picker_state === "judging" && !isReadyToFinalize(s)
  ).length;
  const notStarted = sessions.filter((s) => s.picker_state === "not_started").length;

  const lastEdited = sessions.reduce((acc, s) => {
    if (!s.updated_at) return acc;
    if (!acc || s.updated_at > acc) return s.updated_at;
    return acc;
  }, null);

  return (
    <div
      data-testid="picker-deck-overview"
      style={{
        flex: 1,
        background: "var(--paper)",
        display: "grid",
        gridTemplateRows: "auto auto 1fr auto",
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      {/* Top band */}
      <div
        style={{
          padding: "20px 28px",
          borderBottom: "1px solid var(--ink)",
          background: "var(--paper-soft)",
          display: "flex",
          alignItems: "center",
          gap: 18,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              className="caps"
              style={{ fontSize: 11, color: "var(--ink-3)" }}
            >
              Picker · Deck overview
            </span>
            <span className="pk-chip">{sessions.length} sessions</span>
          </div>
          <div
            className="display"
            style={{
              fontStyle: "italic",
              fontSize: 36,
              fontWeight: 500,
              marginTop: 4,
              lineHeight: 1.1,
            }}
          >
            Untitled deck
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--ink-3)",
              marginTop: 6,
              letterSpacing: "0.05em",
            }}
          >
            last edited {formatRelative(lastEdited)} · {finalized}/
            {sessions.length} sessions finalized · pick a session to enter the
            picker
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn sm primary shadowed"
            disabled={ready === 0}
            onClick={onFinalizeAllReady}
            type="button"
          >
            ★ Finalize all ready ({ready})
          </button>
        </div>
      </div>

      {/* 5-column stats */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(5, 1fr)",
          borderBottom: "1px solid var(--ink)",
          background: "var(--paper)",
        }}
      >
        {[
          {
            lbl: "Total images",
            v: totals.images,
            sub: `across ${sessions.length} session${sessions.length === 1 ? "" : "s"}`,
            dot: null,
          },
          {
            lbl: "Finalized",
            v: finalized,
            sub: "★ locked in slide",
            dot: "final",
          },
          {
            lbl: "Ready to finalize",
            v: ready,
            sub: "1 click to lock",
            dot: null,
            accent: true,
          },
          {
            lbl: "In progress",
            v: judging,
            sub: `${totals.judged} judged so far`,
            dot: "picked",
          },
          {
            lbl: "Not started",
            v: notStarted,
            sub: `${totals.inflight} jobs in flight`,
            dot: "unjudged",
          },
        ].map((s, i) => (
          <div
            key={i}
            style={{
              padding: "16px 20px",
              borderRight: i < 4 ? "1px solid var(--rule-2)" : "none",
              background: s.accent ? "var(--banana-soft)" : "transparent",
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {s.dot && <span className={`pk-state-dot ${s.dot}`} />}
              <span
                className="caps"
                style={{ fontSize: 10, color: "var(--ink-3)" }}
              >
                {s.lbl}
              </span>
            </div>
            <div
              className="display"
              style={{
                fontSize: 32,
                fontWeight: 700,
                lineHeight: 1,
                fontFeatureSettings: "'tnum'",
              }}
            >
              {s.v}
            </div>
            <span
              className="mono"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                letterSpacing: "0.04em",
              }}
            >
              {s.sub}
            </span>
          </div>
        ))}
      </div>

      {/* Session grid */}
      <div
        style={{
          padding: 24,
          overflowY: "auto",
          background: "var(--paper-2)",
        }}
      >
        {sessions.length === 0 ? (
          <div
            style={{
              padding: 60,
              textAlign: "center",
              color: "var(--ink-3)",
              border: "1.5px dashed var(--ink-3)",
              background: "var(--paper-soft)",
            }}
          >
            <div
              className="display"
              style={{
                fontStyle: "italic",
                fontSize: 28,
                marginBottom: 10,
              }}
            >
              No sessions yet
            </div>
            <div style={{ fontSize: 13, marginBottom: 16 }}>
              Head to <strong>Create</strong> to bind your first generation
              into a session.
            </div>
            <button
              className="btn sm primary shadowed"
              onClick={() => navigate("/create")}
              type="button"
            >
              ＋ Create
            </button>
          </div>
        ) : (
          <>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: 14,
              }}
            >
              <span
                className="caps"
                style={{ fontSize: 11, color: "var(--ink-3)" }}
              >
                Sessions in deck order
              </span>
              <span
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)" }}
              >
                click any card to open
              </span>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                gap: 16,
              }}
            >
              {sessions.map((s, i) => (
                <SessionCard
                  key={s.id}
                  index={i}
                  session={s}
                  onClick={() => navigate(`/picker?session_id=${encodeURIComponent(s.id)}`)}
                />
              ))}
            </div>
          </>
        )}
      </div>

      <div
        style={{
          padding: "12px 28px",
          borderTop: "1px solid var(--ink)",
          background: "var(--paper-3)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--ink-3)",
        }}
      >
        <span>← click any session to enter the picker</span>
        <span>Esc returns here from any session</span>
      </div>
    </div>
  );
}

function SessionCard({ session, index, onClick }) {
  const isFinalized = session.picker_state === "finalized";
  const isReady = isReadyToFinalize(session);
  const stats = session.stats || {};
  const total = session.image_count || 0;
  const judged = session.judged_count || 0;
  const inflight =
    (session.in_flight?.queued || 0) +
    (session.in_flight?.running || 0) +
    (session.in_flight?.failed || 0);
  const hasFinal = !!session.final_image_id;
  return (
    <button
      onClick={onClick}
      type="button"
      data-testid={`picker-session-card-${session.id}`}
      style={{
        all: "unset",
        cursor: "pointer",
        border: "1px solid var(--ink)",
        background: isFinalized ? "var(--banana-soft)" : "var(--paper)",
        display: "grid",
        gridTemplateRows: "auto auto auto auto",
        boxShadow: isFinalized
          ? "5px 5px 0 var(--ink)"
          : isReady
          ? "3px 3px 0 var(--ink)"
          : "none",
      }}
    >
      <div
        className="pk-img-pane"
        style={{
          aspectRatio: "16 / 10",
          border: "none",
          borderBottom: "1px solid var(--ink)",
          position: "relative",
          opacity: hasFinal ? 1 : 0.55,
        }}
      >
        {hasFinal && session.final_thumb_url ? (
          <AuthorizedImage
            src={session.final_thumb_url}
            alt=""
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "cover",
            }}
          />
        ) : (
          <div
            className="pk-hatch"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--ink-3)",
            }}
          >
            <span
              className="display"
              style={{ fontStyle: "italic", fontSize: 18 }}
            >
              {session.picker_state === "not_started" ? "not started" : "no final yet"}
            </span>
          </div>
        )}
        <div
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
          {pad2(index + 1)}
        </div>
        {isFinalized && <div className="pk-img-tag banana">★ FINAL</div>}
        {isReady && (
          <div
            className="pk-img-tag"
            style={{
              background: "var(--banana-soft)",
              color: "var(--ink)",
              border: "1px solid var(--ink)",
            }}
          >
            READY
          </div>
        )}
      </div>
      <div style={{ padding: "12px 14px 6px" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
          }}
        >
          <span style={{ fontWeight: 700, fontSize: 16 }}>{session.name}</span>
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            {total} imgs
          </span>
        </div>
        {session.last_prompt && (
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 12,
              color: "var(--ink-3)",
              marginTop: 4,
              lineHeight: 1.35,
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            “{session.last_prompt}”
          </div>
        )}
      </div>
      <div style={{ padding: "0 14px 8px" }}>
        <div className="pk-prog-seg" style={{ height: 6 }}>
          <span
            className="seg-final"
            style={{ width: `${pct(stats.final || 0, total)}%` }}
          />
          <span
            className="seg-picked"
            style={{ width: `${pct(stats.picked || 0, total)}%` }}
          />
          <span
            className="seg-discarded"
            style={{ width: `${pct(stats.discarded || 0, total)}%` }}
          />
          <span
            className="seg-deferred"
            style={{ width: `${pct(stats.deferred || 0, total)}%` }}
          />
          <span className="seg-unjudged" style={{ flex: 1 }} />
        </div>
      </div>
      <div
        style={{
          padding: "6px 14px 12px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--ink-3)",
          letterSpacing: "0.05em",
        }}
      >
        <span>{pct(judged, total)}% judged</span>
        <span>
          {inflight > 0
            ? `${inflight} in flight`
            : (session.picker_state || "not_started").replace(/_/g, " ")}
        </span>
      </div>
    </button>
  );
}
