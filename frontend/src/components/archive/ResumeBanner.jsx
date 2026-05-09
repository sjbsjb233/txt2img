import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as maskDraftDB from "../../storage/maskDraftDB.js";

function relAge(savedAtIso) {
  if (!savedAtIso) return "";
  const t = Date.parse(savedAtIso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const days = Math.floor(hr / 24);
  return `${days} 天前`;
}

/**
 * Banner that lists in-progress mask edits across all images. Shown at
 * the top of the ArchivePage so a user who tabbed away mid-edit can
 * jump straight back without re-painting.
 */
export default function ResumeBanner({ userId }) {
  const [drafts, setDrafts] = useState([]);
  const [expanded, setExpanded] = useState(false);
  const navigate = useNavigate();

  const reload = async () => {
    if (!userId) return;
    try {
      const all = await maskDraftDB.listDrafts(userId);
      setDrafts(all || []);
    } catch {
      setDrafts([]);
    }
  };

  useEffect(() => {
    reload();
    // Refresh when a different tab updates drafts. Storage events
    // don't fire for IDB but do fire for localStorage; we still poll
    // when the page becomes visible again.
    const onFocus = () => reload();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  if (!userId || drafts.length === 0) return null;

  const onResume = (rec) => {
    const qs = rec.mode === "outpaint" ? "?mode=outpaint" : "";
    navigate(`/edit/${rec.parent_hash_id}/${rec.order || 1}${qs}`);
  };

  const onDiscard = async (rec) => {
    const ok = window.confirm(
      `丢弃 #${rec.source_seq_no || rec.parent_hash_id} 的未完成 ${rec.mode} 草稿吗？`
    );
    if (!ok) return;
    try {
      await maskDraftDB.deleteDraft(userId, rec.draft_id);
    } catch {
      // ignored
    }
    reload();
  };

  const renderRow = (rec) => {
    const seqLabel =
      rec.source_seq_no != null ? `#${rec.source_seq_no}` : rec.parent_hash_id.slice(0, 8);
    return (
      <div
        key={rec.draft_id}
        data-testid={`resume-row-${rec.draft_id}`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "8px 0",
          borderTop: "1px dashed var(--rule-2)",
        }}
      >
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--ink-3)", minWidth: 48 }}
        >
          {rec.mode}
        </span>
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 800 }}>
          {seqLabel}
        </span>
        <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          · img {rec.order}
        </span>
        {rec.has_paint && (
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-2)" }}>
            · mask 已绘制
          </span>
        )}
        {rec.prompt && (
          <span
            className="mono"
            style={{
              fontSize: 11,
              color: "var(--ink-2)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              maxWidth: 320,
            }}
          >
            · “{rec.prompt}”
          </span>
        )}
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--ink-3)", marginLeft: "auto" }}
        >
          {relAge(rec.saved_at)}
        </span>
        <button
          data-testid={`resume-${rec.draft_id}`}
          onClick={() => onResume(rec)}
          style={{
            padding: "4px 12px",
            border: "1px solid var(--ink)",
            background: "var(--banana)",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          Resume →
        </button>
        <button
          data-testid={`discard-${rec.draft_id}`}
          onClick={() => onDiscard(rec)}
          style={{
            padding: "4px 12px",
            border: "1px solid var(--ink)",
            background: "var(--card, #fffdf7)",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
          }}
        >
          Discard
        </button>
      </div>
    );
  };

  // One-draft case: show a single inline row.
  if (drafts.length === 1) {
    return (
      <div
        data-testid="archive-resume-banner"
        style={{
          marginTop: 18,
          padding: "10px 14px",
          background: "var(--paper-2)",
          border: "1px solid var(--ink)",
        }}
      >
        <div
          className="mono caps"
          style={{
            fontSize: 9,
            color: "var(--ink-3)",
            letterSpacing: "0.18em",
            marginBottom: 4,
          }}
        >
          IN-PROGRESS MASK EDIT
        </div>
        {renderRow(drafts[0])}
      </div>
    );
  }

  // Multi-draft case: collapsed by default.
  return (
    <div
      data-testid="archive-resume-banner"
      style={{
        marginTop: 18,
        padding: "10px 14px",
        background: "var(--paper-2)",
        border: "1px solid var(--ink)",
      }}
    >
      <div
        className="mono caps"
        style={{
          fontSize: 9,
          color: "var(--ink-3)",
          letterSpacing: "0.18em",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <span>YOU HAVE {drafts.length} IN-PROGRESS MASK EDITS</span>
        <button
          data-testid="resume-banner-toggle"
          onClick={() => setExpanded((e) => !e)}
          style={{
            padding: "2px 8px",
            border: "1px solid var(--ink)",
            background: "var(--card, #fffdf7)",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.06em",
          }}
        >
          {expanded ? "Hide" : "Show"}
        </button>
      </div>
      {expanded && <div style={{ marginTop: 6 }}>{drafts.map(renderRow)}</div>}
    </div>
  );
}
