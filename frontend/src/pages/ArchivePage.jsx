// Archive — the user's full history view, hydrated from IndexedDB and
// kept current via SSE.
//
// State management lives in ``store/archive.js``; this file is only the
// view. The page mounts the store on entry, lets it pull from IndexedDB
// + run a delta sync against the backend, and re-renders as rows update.
//
// What changed from the mockup:
//   - The hard-coded ``HX_IMGS`` mock is gone. Rows come from the
//     real archive store.
//   - SET cards use server ``set_id``; partial-fail cells render when
//     the SUCCEEDED job has fewer images than its declared n.
//   - Click → drawer (single-image jobs) or full-page detail (Set jobs)
//     pulls full details from the store; if a row is missing detail it
//     calls ``refreshDetail()`` on open.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../components/Icon.jsx";
import {
  RunningCard,
  QueuedCard,
  FailCard,
  ArchiveSetCard,
  ArchiveSetDetail,
} from "../components/archive";
import {
  downloadImageFile,
  imageOriginalUrl,
  imageThumbUrl,
  referenceThumbUrl,
} from "../api/archive.js";
import AuthorizedImage from "../components/AuthorizedImage.jsx";
import { useAuthorizedBlobUrls } from "../components/useAuthorizedBlobUrls.js";
import * as archiveStore from "../store/archive.js";
import { useAuth } from "../store/auth.js";

// ---------------------------------------------------------------------------
// Period filter — purely client-side. Picks the cutoff against the
// ``updated_at`` field that's already in the cache.
// ---------------------------------------------------------------------------

const PERIOD_OPTIONS = ["7d", "30d", "90d", "∞"];

function cutoffForPeriod(period) {
  const now = Date.now();
  switch (period) {
    case "7d":
      return now - 7 * 86400_000;
    case "30d":
      return now - 30 * 86400_000;
    case "90d":
      return now - 90 * 86400_000;
    default:
      return 0; // ∞ → show everything
  }
}

// ---------------------------------------------------------------------------
// View-model helpers — turn raw store rows into something the existing
// archive cards can render. We deliberately keep the shape close to the
// old ``HX_IMGS`` records so the legacy card components don't need
// per-field rewrites.
// ---------------------------------------------------------------------------

function shortModel(modelId) {
  if (!modelId) return "";
  if (modelId === "gpt-image-2" || modelId === "gpt-image-2-2026-04-21")
    return "gpt-2";
  if (modelId === "gemini-3-pro-image-preview") return "pro";
  if (modelId === "gemini-3.1-flash-image-preview") return "flash";
  if (modelId.startsWith("gemini-")) return "gemini";
  return modelId.slice(0, 12);
}

function aspectFromImage(img) {
  if (!img?.width || !img?.height) return "1:1";
  const r = img.width / img.height;
  if (Math.abs(r - 1) < 0.05) return "1:1";
  if (Math.abs(r - 16 / 9) < 0.05) return "16:9";
  if (Math.abs(r - 9 / 16) < 0.05) return "9:16";
  if (Math.abs(r - 4 / 3) < 0.05) return "4:3";
  if (Math.abs(r - 3 / 4) < 0.05) return "3:4";
  if (Math.abs(r - 3 / 2) < 0.05) return "3:2";
  if (Math.abs(r - 2 / 3) < 0.05) return "2:3";
  if (Math.abs(r - 21 / 9) < 0.05) return "21:9";
  // Fall back to a literal ratio rounded to 2dp.
  return r >= 1 ? `${r.toFixed(2)}:1` : `1:${(1 / r).toFixed(2)}`;
}

// "12s" / "3m" / "5d ago"
function relativeAge(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.floor(hr / 24);
  return `${d}d`;
}

// "Wed 24 Apr · 14:32:08"
function fullTimestamp(iso) {
  if (!iso) return "—";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "—";
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const pad = (n) => String(n).padStart(2, "0");
  return `${days[t.getDay()]} ${pad(t.getDate())} ${months[t.getMonth()]} · ${pad(
    t.getHours()
  )}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

function formatSeconds(s) {
  if (s == null) return null;
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return `${m}m ${r}s`;
}

// ---------------------------------------------------------------------------
// Group rows into "items" for the grid. A single-image job is one item;
// a Set (multiple jobs sharing a set_id) collapses into one tile that
// represents the whole set.
// ---------------------------------------------------------------------------

function buildItems(rows) {
  const items = [];
  const setBuckets = new Map();
  for (const row of rows) {
    if (row.set_id) {
      if (!setBuckets.has(row.set_id)) {
        setBuckets.set(row.set_id, []);
      }
      setBuckets.get(row.set_id).push(row);
      continue;
    }
    items.push({ kind: "single", row });
  }
  for (const [setId, members] of setBuckets.entries()) {
    members.sort((a, b) => (a.updated_at || "").localeCompare(b.updated_at || ""));
    items.push({ kind: "set", set_id: setId, members });
  }
  // Newest first — for sets, use the freshest member.
  items.sort((a, b) => {
    const ta =
      a.kind === "set"
        ? Math.max(...a.members.map((r) => Date.parse(r.updated_at || 0) || 0))
        : Date.parse(a.row.updated_at || 0) || 0;
    const tb =
      b.kind === "set"
        ? Math.max(...b.members.map((r) => Date.parse(r.updated_at || 0) || 0))
        : Date.parse(b.row.updated_at || 0) || 0;
    return tb - ta;
  });
  return items;
}

// ---------------------------------------------------------------------------
// Single-image card — the "BEST" / generic case from the original
// mockup. Uses the real thumbnail URL from the store.
// ---------------------------------------------------------------------------

function SingleImageCard({ row, focused, onClick }) {
  const img = row.images?.[0];
  const ratio = aspectFromImage(img);
  return (
    <div
      onClick={onClick}
      className={focused ? "arch-focused" : ""}
      style={{
        border: "1px solid var(--ink)",
        background: "#fffdf7",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        outline: focused ? "2px solid var(--bad)" : undefined,
        outlineOffset: focused ? "-2px" : undefined,
      }}
    >
      <div
        style={{
          aspectRatio: "1/1",
          background: img?.thumb_url
            ? "transparent"
            : "var(--paper-2)",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {img?.thumb_url ? (
          <AuthorizedImage
            src={imageThumbUrl(row.hash_id, img.order)}
            alt=""
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              display: "block",
            }}
            onMissing={() => archiveStore.dropLocal(row.hash_id)}
          />
        ) : null}
        {row.images?.some((i) => i.starred) && (
          <span
            style={{
              position: "absolute",
              top: 6,
              right: 6,
              width: 22,
              height: 22,
              background: "var(--banana)",
              border: "1px solid var(--ink)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 13,
              fontWeight: 900,
            }}
          >
            ★
          </span>
        )}
      </div>
      <div
        style={{
          padding: "8px 10px",
          borderTop: "1px solid var(--ink)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--ink-2)",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>
          #{row.seq_no} · {shortModel(row.model)} · {ratio}
        </span>
        <span style={{ color: "var(--ink-3)" }}>
          {relativeAge(row.updated_at)}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter popover — copied from the original mockup; not yet wired to a
// real filter pipeline (period is the only live filter).
// ---------------------------------------------------------------------------

function FilterPopover({ onClose, onPick }) {
  const ref = useRef();
  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    const t = setTimeout(() => document.addEventListener("mousedown", onDoc), 0);
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const props = [
    { id: "period", label: "Period", key: "P", glyph: <Icon name="clock" size={12} /> },
    { id: "model", label: "Model", key: "M", glyph: <span style={{
        width: 14, height: 14, background: "var(--ink)", color: "var(--banana)",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: 9, fontWeight: 800, fontFamily: "var(--font-mono)",
      }}>M</span> },
    { id: "shape", label: "Shape", key: "A", glyph: <span style={{
        width: 14, height: 14, border: "1.5px solid var(--ink)", display: "inline-block",
      }} /> },
    { id: "status", label: "Status", key: "S", glyph: <span style={{
        width: 8, height: 8, borderRadius: "50%", background: "var(--banana-deep)",
        display: "inline-block", margin: "0 3px",
      }} /> },
    { id: "rating", label: "Rating", key: "R", glyph: <span style={{ fontSize: 12, color: "var(--ink)" }}>★</span> },
    { id: "session", label: "Session", key: "N", glyph: <Icon name="grid" size={12} /> },
  ];

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        top: "calc(100% + 8px)",
        left: 0,
        zIndex: 50,
        width: 340,
        background: "#fffdf7",
        border: "1px solid var(--ink)",
        boxShadow: "5px 5px 0 var(--ink)",
        padding: 16,
        animation: "popIn 140ms cubic-bezier(.2,.9,.3,1)",
      }}
    >
      <style>{`
        @keyframes popIn { from { opacity: 0; transform: translateY(-4px) scale(.98); } to { opacity: 1; transform: none; } }
      `}</style>
      <div style={{ position: "relative" }}>
        <input className="inp" placeholder="search filters..." style={{
          paddingLeft: 28, fontFamily: "var(--font-mono)", fontStyle: "italic", color: "var(--ink-3)",
        }}/>
        <div style={{ position: "absolute", top: 11, left: 8, pointerEvents: "none" }}>
          <Icon name="search" size={12} stroke="var(--ink-3)" />
        </div>
      </div>
      <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 14, marginBottom: 4 }}>
        PROPERTIES
      </div>
      {props.map((p) => (
        <button
          key={p.id}
          onClick={() => { onPick(p.id); onClose(); }}
          style={{
            display: "grid", gridTemplateColumns: "20px 1fr 16px",
            gap: 10, alignItems: "center", width: "100%", padding: "8px 4px",
            background: "transparent", border: "none", cursor: "pointer", textAlign: "left",
          }}
        >
          <span style={{ display: "inline-flex" }}>{p.glyph}</span>
          <span style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600 }}>
            {p.label}
          </span>
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)", textAlign: "right" }}>
            {p.key}
          </span>
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right-side detail drawer — single-image jobs only. Sets get the
// full-page ArchiveSetDetail view instead.
// ---------------------------------------------------------------------------

function JobDrawer({ row, onClose, onPrev, onNext, width }) {
  const open = !!row;
  const [render, setRender] = useState(false);

  // Pull full details on open if the row was created via SSE and lacks
  // params/timing/refs.
  useEffect(() => {
    if (open && row && row._hydrated === false) {
      void archiveStore.refreshDetail(row.hash_id);
    }
  }, [open, row?.hash_id, row?._hydrated]);

  useEffect(() => {
    if (open) setRender(true);
    else {
      const t = setTimeout(() => setRender(false), 360);
      return () => clearTimeout(t);
    }
  }, [open]);

  useEffect(() => {
    const onKey = (e) => {
      if (!open) return;
      if (e.key === "Escape") onClose();
      if (e.key === "[") onPrev?.();
      if (e.key === "]") onNext?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, onPrev, onNext]);

  if (!render && !open) return null;
  const it = row || {};
  const img = it.images?.[0];
  const ratio = aspectFromImage(img);
  const shape = img ? `${ratio} · ${img.width}×${img.height}` : ratio;
  const sessionLabel = it.session?.name || "—";
  const queueSec = formatSeconds(it.timing?.queue_seconds);
  const renderSec = formatSeconds(it.timing?.render_seconds);

  return (
    <aside
      style={{
        position: "absolute", top: 0, right: 0, bottom: 0, width,
        background: "var(--paper)", borderLeft: "1px solid var(--ink)",
        transform: open ? "translateX(0)" : "translateX(100%)",
        transition: "transform 360ms cubic-bezier(.22,.85,.22,1)",
        zIndex: 30, display: "flex", flexDirection: "column",
        boxShadow: open ? "-12px 0 32px -16px #19171455" : "none",
        willChange: "transform",
      }}
    >
      <div
        style={{
          padding: "14px 18px", borderBottom: "1px solid var(--ink)",
          display: "flex", alignItems: "center", gap: 10,
        }}
      >
        <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}>
          JOB DETAIL
        </div>
        <span className="display" style={{ fontSize: 16, fontWeight: 800 }}>
          #{it.seq_no}
        </span>
        <span style={{
          width: 10, height: 10, display: "inline-block",
          background:
            it.status === "SUCCEEDED" ? "var(--ok)" :
            it.status === "FAILED" ? "var(--bad)" :
            it.status === "RUNNING" ? "var(--banana)" :
            "var(--ink-3)",
        }} />
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          ⌘[{" "}
          <button onClick={onPrev} style={{
            background: "transparent", border: "none", cursor: "pointer",
            color: "inherit", fontFamily: "inherit", fontSize: "inherit",
          }}>prev</button>{" "}·{" "}
          <button onClick={onNext} style={{
            background: "transparent", border: "none", cursor: "pointer",
            color: "inherit", fontFamily: "inherit", fontSize: "inherit",
          }}>⌘]</button>
        </span>
        <button
          onClick={onClose}
          style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4 }}
        >
          <Icon name="close" size={14} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "20px 22px" }}>
        <div
          style={{
            aspectRatio: "16/11",
            background: img?.thumb_url ? "transparent" : "var(--paper-2)",
            border: "1px solid var(--ink)",
            position: "relative",
            overflow: "hidden",
          }}
        >
          {img?.thumb_url ? (
            <AuthorizedImage
              src={imageThumbUrl(it.hash_id, img.order)}
              alt=""
              style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
            />
          ) : null}
        </div>

        <div style={{ marginTop: 16, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {img && (
            <button
              onClick={() =>
                downloadImageFile(
                  imageOriginalUrl(it.hash_id, img.order),
                  `${it.hash_id}_${String(img.order).padStart(2, "0")}.${img.format || "bin"}`
                )
              }
              className="btn sm ink"
              style={{ padding: "0 12px", textDecoration: "none" }}
            >
              <Icon name="download" size={11} stroke="var(--banana)" />
              <span style={{ color: "var(--banana)" }}>download</span>
            </button>
          )}
          {img && (
            <button
              className="btn sm"
              onClick={() => archiveStore.toggleStar(it.hash_id, img.order)}
            >
              {img.starred ? "★ unpick" : "★ pick"}
            </button>
          )}
        </div>

        <div style={{ marginTop: 22 }}>
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.16em" }}>
            PROMPT
          </div>
          <div
            style={{
              marginTop: 8, padding: "12px 14px", background: "var(--paper-2)",
              border: "1px solid var(--ink)", fontFamily: "var(--font-mono)",
              fontSize: 12, lineHeight: 1.55, position: "relative",
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}
          >
            {it.prompt || "—"}
            {it.prompt && (
              <button
                onClick={() => navigator.clipboard?.writeText(it.prompt)}
                style={{
                  position: "absolute", bottom: 6, right: 8,
                  background: "transparent", border: "none", cursor: "pointer",
                  fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)",
                }}
              >
                copy
              </button>
            )}
          </div>
        </div>

        <div style={{ marginTop: 22 }}>
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.16em" }}>
            META
          </div>
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}>
            <MetaRow k="model" v={it.model_display_name || it.model} />
            <MetaRow k="shape" v={shape} />
            {queueSec && <MetaRow k="queue" v={queueSec} />}
            {renderSec && <MetaRow k="render" v={renderSec} />}
            <MetaRow k="created" v={fullTimestamp(it.timing?.queued_at || it.updated_at)} />
            {it.references?.length ? (
              <MetaRow
                k="refs"
                v={`used ${it.references.length} reference image${
                  it.references.length === 1 ? "" : "s"
                }`}
              />
            ) : null}
            <MetaRow k="session" v={sessionLabel} link={!!it.session} />
          </div>
        </div>

        {Object.keys(it.params || {}).length > 0 && (
          <div style={{ marginTop: 22 }}>
            <details>
              <summary
                className="mono caps"
                style={{
                  fontSize: 10, color: "var(--ink-3)", cursor: "pointer",
                  outline: "none", userSelect: "none",
                }}
              >
                ◢ More details
              </summary>
              <div
                style={{
                  marginTop: 10, padding: "10px 12px", background: "var(--paper-2)",
                  border: "1px solid var(--ink)", fontFamily: "var(--font-mono)",
                  fontSize: 11,
                }}
              >
                {Object.entries(it.params).map(([k, v]) => (
                  <div
                    key={k}
                    style={{
                      display: "flex", justifyContent: "space-between",
                      padding: "4px 0", borderBottom: "1px dashed var(--rule-2)",
                    }}
                  >
                    <span style={{ color: "var(--ink-3)" }}>{k}</span>
                    <span style={{ fontWeight: 600 }}>{String(v)}</span>
                  </div>
                ))}
              </div>
            </details>
          </div>
        )}

        {it.references?.length > 0 && (
          <div style={{ marginTop: 22 }}>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.16em" }}>
              REFERENCES
            </div>
            <div
              style={{
                marginTop: 10, display: "grid",
                gridTemplateColumns: "repeat(5, 1fr)", gap: 6,
              }}
            >
              {it.references.map((r) => (
                <div
                  key={r.order}
                  style={{
                    aspectRatio: "1/1",
                    border: "1px solid var(--ink)",
                    background: "var(--paper-2)",
                    overflow: "hidden",
                  }}
                >
                  <AuthorizedImage
                    src={referenceThumbUrl(it.hash_id, r.order)}
                    alt={r.filename}
                    style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {it.error && (
          <div style={{ marginTop: 22 }}>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--bad)", letterSpacing: "0.16em" }}>
              ERROR
            </div>
            <div
              style={{
                marginTop: 8, padding: "10px 12px", background: "#fdecea",
                border: "1px solid var(--bad)", fontFamily: "var(--font-mono)",
                fontSize: 11, color: "var(--bad)",
              }}
            >
              {it.error}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function MetaRow({ k, v, link = false }) {
  return (
    <div
      style={{
        display: "flex", justifyContent: "space-between",
        padding: "6px 0", borderBottom: "1px dashed var(--rule-2)",
      }}
    >
      <span style={{ color: "var(--ink-3)" }}>{k}</span>
      <span
        style={{
          fontWeight: 600,
          color: link ? "var(--info)" : "var(--ink)",
          textDecoration: link ? "underline" : "none",
        }}
      >
        {v}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ArchivePage() {
  const { user } = useAuth();
  const userId = user?.id || null;

  // App-level mount in App.jsx handles the actual lifecycle; this
  // effect is only here so a fresh archive page kicks a delta sync
  // even if App's effect ran long ago. ``mount()`` is idempotent
  // when the user id matches.
  useEffect(() => {
    if (!userId) return;
    void archiveStore.mount(userId);
  }, [userId]);

  const allRows = archiveStore.useArchive();

  // -----------------------------------------------------------------
  // Filters / view state
  // -----------------------------------------------------------------
  const [period, setPeriod] = useState("7d");
  const [filterOpen, setFilterOpen] = useState(false);
  const [drawerHash, setDrawerHash] = useState(null);
  const [setDetailId, setSetDetailId] = useState(null);
  const [searchParams] = useSearchParams();
  const sessionFilter = searchParams.get("session_id");

  // RUNNING cards need a per-second tick. One global ticker beats N
  // mounted intervals; the cards read off the parent state.
  const [, setRunTick] = useState(0);
  useEffect(() => {
    const i = setInterval(() => setRunTick((t) => t + 1), 1000);
    return () => clearInterval(i);
  }, []);

  // -----------------------------------------------------------------
  // Derived row list
  // -----------------------------------------------------------------
  const cutoff = useMemo(() => cutoffForPeriod(period), [period]);
  const filteredRows = useMemo(() => {
    return allRows.filter((row) => {
      if (sessionFilter && row.session_id !== sessionFilter) {
        return false;
      }
      if (cutoff > 0) {
        const t = Date.parse(row.updated_at || 0) || 0;
        if (t < cutoff) return false;
      }
      return true;
    });
  }, [allRows, sessionFilter, cutoff]);

  const items = useMemo(() => buildItems(filteredRows), [filteredRows]);

  // Pre-fetch blob URLs for every thumbnail referenced by Set tiles +
  // single-image cards. Set tiles render via background-image which
  // can't carry an Authorization header, so the blob detour is the only
  // way to get them displayed.
  const allThumbSources = useMemo(() => {
    const out = [];
    for (const it of items) {
      if (it.kind === "set") {
        for (const member of it.members) {
          for (const img of member.images || []) {
            out.push(imageThumbUrl(member.hash_id, img.order));
          }
        }
      } else {
        const img = it.row.images?.[0];
        if (img) out.push(imageThumbUrl(it.row.hash_id, img.order));
      }
    }
    return out;
  }, [items]);
  const blobByUrl = useAuthorizedBlobUrls(allThumbSources);

  // -----------------------------------------------------------------
  // Drawer + set detail navigation
  // -----------------------------------------------------------------
  const drawerRow = drawerHash ? archiveStore.getRow(drawerHash) : null;
  const drawerOpen = !!drawerRow;
  const drawerWidth = 460;

  const baseCols = 4;
  const cols = drawerOpen ? Math.max(3, baseCols - 1) : baseCols;

  const onItemClick = useCallback((item) => {
    if (item.kind === "set") {
      setDrawerHash(null);
      setSetDetailId(item.set_id);
    } else {
      setSetDetailId(null);
      setDrawerHash(item.row.hash_id);
    }
  }, []);

  const close = () => setDrawerHash(null);
  const closeSetDetail = () => setSetDetailId(null);

  const singleItems = items.filter((it) => it.kind === "single");
  const idx = drawerHash
    ? singleItems.findIndex((it) => it.row.hash_id === drawerHash)
    : -1;
  const prev = () => {
    if (idx > 0) setDrawerHash(singleItems[idx - 1].row.hash_id);
  };
  const next = () => {
    if (idx >= 0 && idx < singleItems.length - 1) {
      setDrawerHash(singleItems[idx + 1].row.hash_id);
    }
  };

  // -----------------------------------------------------------------
  // Title scaling — copied from the original mockup
  // -----------------------------------------------------------------
  const titleWrap = useRef(null);
  const titleEl = useRef(null);
  const [titleScale, setTitleScale] = useState(1);
  useLayoutEffect(() => {
    let raf;
    const measure = () => {
      if (!titleWrap.current || !titleEl.current) return;
      titleEl.current.style.transform = "scale(1)";
      const w = titleWrap.current.clientWidth;
      const natural = titleEl.current.scrollWidth;
      const s = natural > w ? Math.max(0.4, w / natural) : 1;
      setTitleScale(s);
    };
    raf = requestAnimationFrame(measure);
    const ro = new ResizeObserver(() => requestAnimationFrame(measure));
    if (titleWrap.current) ro.observe(titleWrap.current);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [drawerOpen]);

  // -----------------------------------------------------------------
  // SET full-page detail view
  // -----------------------------------------------------------------
  if (setDetailId) {
    const setItem = items.find((it) => it.kind === "set" && it.set_id === setDetailId);
    if (!setItem) {
      // The set vanished while the detail view was open (cleanup, etc).
      setSetDetailId(null);
      return null;
    }
    const allImages = setItem.members.flatMap((row) =>
      (row.images || []).map((img) => ({
        ...img,
        _ownerHash: row.hash_id,
        _ownerSeq: row.seq_no,
      }))
    );
    const firstRow = setItem.members[0];
    return (
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          background: "var(--paper)",
          padding: "32px 56px 60px",
        }}
      >
        <ArchiveSetDetail
          id={firstRow?.seq_no}
          model={firstRow?.model_display_name || firstRow?.model}
          age={`${relativeAge(firstRow?.updated_at)} ago`}
          prompt={firstRow?.prompt || ""}
          panelCount={allImages.length}
          panels={allImages.map((img) => ({
            src:
              blobByUrl[imageThumbUrl(img._ownerHash, img.order)] ||
              imageThumbUrl(img._ownerHash, img.order),
            title: `#${img._ownerSeq}`,
            starred: !!img.starred,
          }))}
          onBack={closeSetDetail}
        />
      </div>
    );
  }

  // -----------------------------------------------------------------
  // Empty / hydrating states
  // -----------------------------------------------------------------
  const showEmpty = !!userId && allRows.length === 0;

  return (
    <div
      style={{
        flex: 1,
        overflow: "hidden",
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}
    >
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          paddingRight: drawerOpen ? drawerWidth : 0,
          transition: "padding-right 360ms cubic-bezier(.22,.85,.22,1)",
          willChange: "padding-right",
        }}
      >
        <div style={{ padding: "32px 56px 60px" }}>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}>
            HISTORY · ARCHIVE
          </div>

          <div ref={titleWrap} style={{ marginTop: 6, overflow: "hidden", paddingBottom: 2 }}>
            <h1
              ref={titleEl}
              className="display"
              style={{
                fontSize: 42, fontWeight: 900, letterSpacing: "-0.035em",
                lineHeight: 1.15, margin: 0, whiteSpace: "nowrap",
                display: "inline-block", transform: `scale(${titleScale})`,
                transformOrigin: "left center",
                transition: "transform 360ms cubic-bezier(.22,.85,.22,1)",
                willChange: "transform",
              }}
            >
              Everything you've made.
            </h1>
          </div>

          <div
            style={{
              marginTop: 14, display: "flex", alignItems: "center",
              gap: 14, flexWrap: "nowrap", minHeight: 40,
            }}
          >
            <div
              style={{
                position: "relative", display: "flex",
                alignItems: "center", gap: 10, flexShrink: 0,
              }}
            >
              <button
                onClick={() => {
                  const idx = PERIOD_OPTIONS.indexOf(period);
                  const nextOpt = PERIOD_OPTIONS[(idx + 1) % PERIOD_OPTIONS.length];
                  setPeriod(nextOpt);
                }}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "8px 14px", borderRadius: 999,
                  background: "var(--ink)", color: "var(--paper)",
                  border: "1px solid var(--ink)", cursor: "pointer",
                  fontFamily: "var(--font-mono)", fontSize: 12,
                  fontWeight: 600, whiteSpace: "nowrap",
                }}
              >
                period <span style={{ fontWeight: 800 }}>{period}</span>
              </button>

              <div style={{ position: "relative" }}>
                <button
                  onClick={() => setFilterOpen(!filterOpen)}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "8px 14px", borderRadius: 999,
                    background: "transparent", color: "var(--ink-3)",
                    border: "1px dashed var(--ink-3)", cursor: "pointer",
                    fontFamily: "var(--font-mono)", fontSize: 12,
                    fontWeight: 600, whiteSpace: "nowrap",
                  }}
                >
                  + filter
                </button>
                {filterOpen && (
                  <FilterPopover onClose={() => setFilterOpen(false)} onPick={() => {}} />
                )}
              </div>
            </div>

            <div
              style={{
                display: "flex", alignItems: "baseline", gap: 8,
                marginLeft: 4, flexShrink: 0, whiteSpace: "nowrap",
              }}
            >
              <span className="ticker" style={{ fontSize: 22, fontWeight: 900, letterSpacing: "-0.03em" }}>
                {items.length}
              </span>
              <span className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
                matches
              </span>
              {!drawerOpen && period !== "∞" && (
                <span style={{
                  fontFamily: "var(--font-display)",
                  fontStyle: "italic", fontSize: 14, color: "var(--ink-3)",
                }}>
                  over {period === "7d" ? "7 days" : period === "30d" ? "30 days" : "90 days"}
                </span>
              )}
            </div>

            <div
              style={{
                marginLeft: "auto", display: "flex", alignItems: "center",
                gap: 10, flexShrink: 0,
              }}
            >
              <button
                onClick={() => archiveStore.sync()}
                title="Refresh from server"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "6px 10px", height: 32,
                  background: "#fffdf7", border: "1px solid var(--ink)",
                  cursor: "pointer", fontFamily: "var(--font-mono)",
                  fontSize: 11, fontWeight: 600,
                }}
              >
                ↻ sync
              </button>
            </div>
          </div>

          {showEmpty && (
            <div
              className="mono"
              style={{
                marginTop: 80,
                padding: "40px 0",
                textAlign: "center",
                color: "var(--ink-3)",
                fontSize: 14,
              }}
            >
              <div style={{ fontFamily: "var(--font-display)", fontSize: 28, marginBottom: 8 }}>
                No jobs yet.
              </div>
              <div>Hit Create to make your first one.</div>
            </div>
          )}

          {!showEmpty && (
            <div
              style={{
                marginTop: 28, display: "grid",
                gridTemplateColumns: `repeat(${cols}, 1fr)`,
                gap: 12,
                transition: "grid-template-columns 360ms cubic-bezier(.22,.85,.22,1)",
              }}
            >
              {items.map((item) => {
                if (item.kind === "set") {
                  // Aggregated multi-image card. Render the cells from
                  // every member's images, in stable order. We translate
                  // the API URL into a blob URL so the SET card's
                  // background-image picks up something the browser can
                  // actually fetch (the API URL needs a bearer token).
                  const images = item.members.flatMap((row) =>
                    (row.images || []).map((img) => {
                      const apiUrl = imageThumbUrl(row.hash_id, img.order);
                      return { src: blobByUrl[apiUrl] || undefined };
                    })
                  );
                  const stillRunning = item.members.some(
                    (r) => r.status === "QUEUED" || r.status === "RUNNING"
                  );
                  const newest = item.members.reduce((acc, r) => {
                    const t = Date.parse(r.updated_at || 0) || 0;
                    return t > acc.t ? { row: r, t } : acc;
                  }, { row: item.members[0], t: 0 });
                  return (
                    <ArchiveSetCard
                      key={item.set_id}
                      size="grid"
                      id={newest.row.seq_no}
                      model={shortModel(newest.row.model)}
                      age={relativeAge(newest.row.updated_at)}
                      images={images.length > 0 ? images : item.members.map(() => ({}))}
                      totalCount={images.length || item.members.length}
                      running={stillRunning}
                      onClick={() => onItemClick(item)}
                    />
                  );
                }

                const row = item.row;
                const focused = drawerHash === row.hash_id;
                const focusClass = focused ? "arch-focused" : "";

                if (row.status === "RUNNING") {
                  const elapsed = row._runStartedAt
                    ? Math.max(0, Math.floor((Date.now() - row._runStartedAt) / 1000))
                    : 0;
                  return (
                    <RunningCard
                      key={row.hash_id}
                      size="grid"
                      id={row.seq_no}
                      model={shortModel(row.model)}
                      ratio={aspectFromImage(row.images?.[0])}
                      seconds={elapsed}
                      onClick={() => onItemClick(item)}
                      className={focusClass}
                    />
                  );
                }

                if (row.status === "QUEUED") {
                  return (
                    <QueuedCard
                      key={row.hash_id}
                      size="grid"
                      id={row.seq_no}
                      model={shortModel(row.model)}
                      ratio={aspectFromImage(row.images?.[0])}
                      position={row._position || 1}
                      eta={row._eta != null ? `~ ${row._eta}s` : undefined}
                      onClick={() => onItemClick(item)}
                      className={focusClass}
                    />
                  );
                }

                if (row.status === "FAILED") {
                  return (
                    <FailCard
                      key={row.hash_id}
                      size="grid"
                      id={row.seq_no}
                      model={shortModel(row.model)}
                      ratio={aspectFromImage(row.images?.[0])}
                      age={relativeAge(row.updated_at)}
                      label="failed"
                      reason={row.error || row.status_reason || ""}
                      onClick={() => onItemClick(item)}
                      className={focusClass}
                    />
                  );
                }

                if (row.status === "CANCELLED") {
                  return (
                    <FailCard
                      key={row.hash_id}
                      size="grid"
                      id={row.seq_no}
                      model={shortModel(row.model)}
                      ratio={aspectFromImage(row.images?.[0])}
                      age={relativeAge(row.updated_at)}
                      chipText="CANCELLED"
                      label="cancelled"
                      reason="user cancelled"
                      onClick={() => onItemClick(item)}
                      className={focusClass}
                    />
                  );
                }

                // SUCCEEDED — single image card
                return (
                  <SingleImageCard
                    key={row.hash_id}
                    row={row}
                    focused={focused}
                    onClick={() => onItemClick(item)}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>

      <JobDrawer
        row={drawerRow}
        onClose={close}
        onPrev={prev}
        onNext={next}
        width={drawerWidth}
      />
    </div>
  );
}
