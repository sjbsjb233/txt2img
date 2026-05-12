// Archive — the user's full history view, hydrated from IndexedDB and
// kept current via SSE.
//
// View-layer composition: this file wires together small, focused
// modules. Filter pipeline + sorting live in `archiveFilter.js`;
// pagination is a hook (`useArchivePagination`); the toolbar is split
// into ChipStrip / FilterPopover / SortDropdown / PaginationPager.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon.jsx";
import { supportsMaskEdit, hasUsedMaskEdit } from "../config/maskEdit.js";
import {
  RunningCard,
  QueuedCard,
  FailCard,
  ArchiveSetCard,
  ArchiveSetDetail,
  ArchiveEmptyHero,
  Lightbox,
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
import {
  useArchiveFilters,
  useArchiveSort,
  EMPTY_FILTER,
} from "../store/archivePrefs.js";
import {
  shortModel,
  aspectFromImage,
  filterRows,
  buildItems,
  sortItems,
  computeFilterOptions,
  chipCount as chipCountFn,
  computeSourceBadge,
} from "../components/archive/archiveFilter.js";
import SourceBadge from "../components/archive/SourceBadge.jsx";
import MixedSourceBadge from "../components/archive/MixedSourceBadge.jsx";
import FilterPopover from "../components/archive/FilterPopover.jsx";
import SortDropdown from "../components/archive/SortDropdown.jsx";
import ChipStrip, { FilterTrigger } from "../components/archive/ChipStrip.jsx";
import PaginationPager from "../components/archive/PaginationPager.jsx";
import PullMembrane from "../components/archive/PullMembrane.jsx";
import ArchiveNoMatches from "../components/archive/ArchiveNoMatches.jsx";
import ResumeBanner from "../components/archive/ResumeBanner.jsx";
import useArchivePagination from "../hooks/useArchivePagination.js";

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------

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

function fullTimestamp(iso) {
  if (!iso) return "—";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "—";
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const pad = (n) => String(n).padStart(2, "0");
  return `${days[t.getDay()]} ${pad(t.getDate())} ${months[t.getMonth()]} · ${pad(
    t.getHours()
  )}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

// Number of image slots a SET member should occupy in the contact sheet.
// Takes the max of the user-requested ``n`` and the count we actually
// received — so a SUCCEEDED member whose upstream returned fewer images
// than requested still shows all ``n`` slots (the extras render as
// partial-fail placeholders, matching how RUNNING shows ``n`` running
// slots). Falls back to images.length, then 1.
function expectedImagesForMember(row) {
  const stored = Array.isArray(row?.images) ? row.images.length : 0;
  const declared =
    (row?.image_count && row.image_count > 0 ? row.image_count : null) ??
    row?.params?.n ??
    row?.params?.batch_size ??
    row?.batch_size;
  const declaredN =
    typeof declared === "number" && declared > 0 ? declared : 0;
  return Math.max(stored, declaredN, 1);
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
// Single-image card
// ---------------------------------------------------------------------------

function SingleImageCard({ row, focused, onClick, sourceBadge = null }) {
  const img = row.images?.[0];
  const ratio = aspectFromImage(img);
  return (
    <div
      data-testid={`card-single-${row.hash_id}`}
      data-hash={row.hash_id}
      data-status={row.status}
      data-seq={row.seq_no}
      data-starred={row.images?.some((i) => i.starred) ? "true" : "false"}
      data-session={row.session?.id || row.session_id || ""}
      onClick={onClick}
      className={focused ? "arch-focused" : ""}
      style={{
        border: "1px solid var(--ink)",
        background: "var(--card, #fffdf7)",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        outline: focused ? "2px solid var(--bad)" : undefined,
        outlineOffset: focused ? "-2px" : undefined,
      }}
    >
      <div
        data-thumb="1"
        style={{
          aspectRatio: "1/1",
          background: img?.thumb_url ? "transparent" : "var(--paper-2)",
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
              left: 6,
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
        {sourceBadge}
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
// Right-side detail drawer
// ---------------------------------------------------------------------------

function JobDrawer({ row, onClose, onPrev, onNext, onOpenLightbox, width, imageIndex = 0 }) {
  const navigate = useNavigate();
  const open = !!row;
  const [render, setRender] = useState(false);

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
  const imgs = it.images || [];
  const safeIdx = Math.max(0, Math.min(imageIndex, imgs.length - 1));
  const img = imgs[safeIdx] || imgs[0];
  const ratio = aspectFromImage(img);
  const shape = img ? `${ratio} · ${img.width}×${img.height}` : ratio;
  const sessionLabel = it.session?.name || "—";
  const queueSec = formatSeconds(it.timing?.queue_seconds);
  const renderSec = formatSeconds(it.timing?.render_seconds);

  return (
    <aside
      data-testid="archive-drawer"
      data-row={it.hash_id || ""}
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
          }}>next</button>{" "}⌘]
        </span>
        <button
          data-testid="drawer-close"
          onClick={onClose}
          style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4 }}
        >
          <Icon name="close" size={14} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "20px 22px" }}>
        <DrawerPreviewStage
          row={it}
          img={img}
          ratio={ratio}
          onOpen={img?.thumb_url ? onOpenLightbox : undefined}
        />

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
              data-testid="drawer-star"
              className="btn sm"
              onClick={() => archiveStore.toggleStar(it.hash_id, img.order)}
            >
              {img.starred ? "★ unpick" : "★ pick"}
            </button>
          )}
          {img && it.status === "SUCCEEDED" && supportsMaskEdit(it.model) && (
            <button
              data-testid="drawer-edit"
              className="btn sm primary shadowed"
              style={{ position: "relative" }}
              onClick={() => navigate(`/edit/${it.hash_id}/${img.order}`)}
              title="Open mask editor (NEW)"
            >
              <Icon name="image" size={11} />
              <span>edit with mask</span>
              {!hasUsedMaskEdit() && (
                <>
                  <span className="me-new-badge" data-testid="drawer-edit-new-badge">NEW</span>
                  <span className="me-edit-tooltip" data-testid="drawer-edit-tooltip">
                    opens the mask editor in a takeover view
                  </span>
                </>
              )}
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

// Drawer's hero image — renders the thumb at its natural aspect inside
// a fixed-height frame so 9:16/16:9/1:1 all show fully without cropping.
function DrawerPreviewStage({ row, img, ratio, onOpen }) {
  const naturalRatio =
    img?.width && img?.height ? `${img.width} / ${img.height}` : "1 / 1";
  const dim = img && img.width && img.height ? `${img.width}×${img.height}` : null;
  const interactive = !!onOpen;
  const handleKey = (e) => {
    if (!interactive) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen?.();
    }
  };
  return (
    <div
      data-testid="drawer-preview-stage"
      className="drawer-preview-stage"
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? "Open fullscreen preview" : undefined}
      onClick={interactive ? () => onOpen?.() : undefined}
      onKeyDown={handleKey}
      style={{
        width: "100%",
        maxHeight: "min(56vh, 520px)",
        border: "1px solid var(--ink)",
        background: "var(--paper-2)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
        position: "relative",
        cursor: interactive ? "zoom-in" : "default",
      }}
    >
      {img?.thumb_url ? (
        <AuthorizedImage
          src={imageThumbUrl(row.hash_id, img.order)}
          alt=""
          style={{
            maxWidth: "100%",
            maxHeight: "min(56vh, 520px)",
            aspectRatio: naturalRatio,
            objectFit: "contain",
            display: "block",
          }}
        />
      ) : (
        <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}>
          no preview
        </div>
      )}
      {img && dim && (
        <span
          className="mono"
          data-testid="drawer-preview-badge"
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            background: "var(--ink)",
            color: "var(--paper)",
            fontSize: 9,
            padding: "2px 6px",
            letterSpacing: "0.06em",
          }}
        >
          {ratio} · {dim}
        </span>
      )}
      {interactive && (
        <span
          className="drawer-preview-hint mono"
          style={{
            position: "absolute",
            bottom: 6,
            right: 6,
            background: "rgba(25,23,20,0.85)",
            color: "var(--paper)",
            fontSize: 9,
            padding: "2px 6px",
            letterSpacing: "0.04em",
            opacity: 0,
            transition: "opacity 140ms ease",
            pointerEvents: "none",
          }}
        >
          ⊕ click to expand
        </span>
      )}
    </div>
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
  const navigate = useNavigate();

  useEffect(() => {
    if (!userId) return;
    void archiveStore.mount(userId);
  }, [userId]);

  const allRows = archiveStore.useArchive();
  const rowsByHashId = useMemo(() => {
    const map = new Map();
    for (const r of allRows || []) {
      if (r?.hash_id) map.set(r.hash_id, r);
    }
    return map;
  }, [allRows]);
  const { applied, setApplied } = useArchiveFilters();
  const [sortKey, setSortKey] = useArchiveSort();

  const [searchParams] = useSearchParams();
  const sessionFilter = searchParams.get("session_id");
  const sessionLockedByUrl = !!sessionFilter;

  const [filterOpen, setFilterOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [drawerHash, setDrawerHash] = useState(null);
  const [setDetailId, setSetDetailId] = useState(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  // SET detail: which panel (global index, 0-based) is selected.
  // Persists after the drawer is dismissed so the panel stays highlighted.
  const [setDetailPanelIdx, setSetDetailPanelIdx] = useState(null);
  // SET detail: whether the right-side drawer is currently rendered.
  // Decoupled from panel selection so ESC can dismiss drawer without
  // losing focus, and a second ESC exits the detail view.
  const [setDetailDrawerOpen, setSetDetailDrawerOpen] = useState(false);
  // SET detail: 1-based page when panel count > pageSize.
  const [setDetailPage, setSetDetailPage] = useState(1);

  useEffect(() => {
    // Reset panel + page + lightbox state when entering / leaving a SET.
    setSetDetailPanelIdx(null);
    setSetDetailDrawerOpen(false);
    setSetDetailPage(1);
    setLightboxOpen(false);
  }, [setDetailId]);

  // RUNNING cards need a per-second tick.
  const [, setRunTick] = useState(0);
  useEffect(() => {
    const i = setInterval(() => setRunTick((t) => t + 1), 1000);
    return () => clearInterval(i);
  }, []);

  // Effective applied filter: URL sessionId always wins.
  const effectiveApplied = useMemo(() => {
    if (sessionFilter) {
      return { ...applied, session: sessionFilter };
    }
    return applied;
  }, [applied, sessionFilter]);

  const dynamicOptions = useMemo(() => computeFilterOptions(allRows), [allRows]);

  // Filter pipeline: rows → filter → buildItems → sort.
  const filteredRows = useMemo(
    () => filterRows(allRows, effectiveApplied),
    [allRows, effectiveApplied]
  );
  const items = useMemo(() => buildItems(filteredRows), [filteredRows]);
  const sortedItems = useMemo(() => sortItems(items, sortKey), [items, sortKey]);

  // Pagination
  const scrollerRef = useRef(null);
  const stageRef = useRef(null);
  const pagination = useArchivePagination({
    scrollerRef,
    items: sortedItems,
  });
  const {
    visibleByPage,
    visibleItems,
    pull,
    isCommitting,
    viewportPage,
    totalPages,
    loadedPages,
    jumpTo,
    reset: resetPagination,
  } = pagination;

  // Reset pagination when filter / sort signature changes.
  const filterSignature = useMemo(() => JSON.stringify(effectiveApplied), [effectiveApplied]);
  useEffect(() => {
    resetPagination();
  }, [filterSignature, sortKey, resetPagination]);

  // Pre-fetch blob URLs for visible items only. When the SET detail
  // is open we also prefetch every member of the focused SET so the
  // panel grid hydrates as fast as the list grid does.
  const visibleThumbSources = useMemo(() => {
    const out = [];
    for (const it of visibleItems) {
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
    if (setDetailId) {
      const setItem = sortedItems.find(
        (it) => it.kind === "set" && it.set_id === setDetailId
      );
      if (setItem) {
        for (const m of setItem.members) {
          for (const img of m.images || []) {
            out.push(imageThumbUrl(m.hash_id, img.order));
          }
        }
      }
    }
    return out;
  }, [visibleItems, setDetailId, sortedItems]);
  const blobByUrl = useAuthorizedBlobUrls(visibleThumbSources);

  // Drawer + set detail navigation
  const drawerRow = drawerHash ? archiveStore.getRow(drawerHash) : null;
  const drawerOpen = !!drawerRow;
  const drawerWidth = 460;

  const onItemClick = useCallback((item) => {
    if (item.kind === "set") {
      setDrawerHash(null);
      setSetDetailId(item.set_id);
    } else {
      setSetDetailId(null);
      setDrawerHash(item.row.hash_id);
    }
  }, []);

  const close = () => {
    setDrawerHash(null);
    setLightboxOpen(false);
  };
  const closeSetDetail = () => setSetDetailId(null);

  // [/] in drawer navigates among visible single items only.
  const visibleSingles = useMemo(
    () => visibleItems.filter((it) => it.kind === "single"),
    [visibleItems]
  );
  const idx = drawerHash
    ? visibleSingles.findIndex((it) => it.row.hash_id === drawerHash)
    : -1;
  const prev = () => {
    if (idx > 0) setDrawerHash(visibleSingles[idx - 1].row.hash_id);
  };
  const next = () => {
    if (idx >= 0 && idx < visibleSingles.length - 1) {
      setDrawerHash(visibleSingles[idx + 1].row.hash_id);
    }
  };

  // Title scaling
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

  // Toolbar refs — declared up front so the conditional return below
  // never makes the hook count vary between renders.
  const filterTriggerRef = useRef(null);
  const sortTriggerRef = useRef(null);

  // ESC handler for the SET detail page. JobDrawer already owns ESC
  // while the drawer is open (it closes the drawer); this fires only
  // when the drawer is closed and exits the detail view entirely.
  useEffect(() => {
    if (!setDetailId || setDetailDrawerOpen) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        setSetDetailId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setDetailId, setDetailDrawerOpen]);

  // SET full-page detail
  if (setDetailId) {
    const setItem = sortedItems.find(
      (it) => it.kind === "set" && it.set_id === setDetailId
    );
    if (!setItem) {
      setSetDetailId(null);
      return null;
    }
    // Expand SET into one panel per expected image. RUNNING / FAILED
    // members emit placeholder panels so the grid mirrors the SET card.
    const detailPanels = [];
    for (const member of setItem.members) {
      const expected = expectedImagesForMember(member);
      if (member.status === "FAILED" || member.status === "CANCELLED") {
        for (let k = 0; k < expected; k++) {
          detailPanels.push({
            ownerRow: member,
            ownerImage: null,
            title: `#${member.seq_no}`,
            state: "fail",
          });
        }
        continue;
      }
      if (member.status === "QUEUED" || member.status === "RUNNING") {
        for (let k = 0; k < expected; k++) {
          detailPanels.push({
            ownerRow: member,
            ownerImage: null,
            title: `#${member.seq_no}`,
            state: "running",
          });
        }
        continue;
      }
      const imgs = member.images || [];
      if (imgs.length === 0) {
        for (let k = 0; k < expected; k++) {
          detailPanels.push({
            ownerRow: member,
            ownerImage: null,
            title: `#${member.seq_no}`,
            state: "loading",
          });
        }
        continue;
      }
      for (const img of imgs) {
        const apiUrl = imageThumbUrl(member.hash_id, img.order);
        const blob = blobByUrl[apiUrl];
        detailPanels.push({
          ownerRow: member,
          ownerImage: img,
          title: `#${member.seq_no}`,
          starred: !!img.starred,
          src: blob || apiUrl,
          state: blob ? "done" : "loading",
        });
      }
      // Upstream returned fewer images than requested (relay quirk on some
      // gpt-image-2 providers). Surface the gap as "missing" slots so the
      // SET tile still matches ``params.n`` — otherwise the badge would
      // silently halve after generation finishes.
      for (let k = imgs.length; k < expected; k++) {
        detailPanels.push({
          ownerRow: member,
          ownerImage: null,
          title: `#${member.seq_no}`,
          state: "fail",
        });
      }
    }
    const firstRow = setItem.members[0];
    const drawerPanel =
      setDetailDrawerOpen && setDetailPanelIdx != null
        ? detailPanels[setDetailPanelIdx] || null
        : null;
    const setDrawerRow = drawerPanel?.ownerRow
      ? archiveStore.getRow(drawerPanel.ownerRow.hash_id) || drawerPanel.ownerRow
      : null;
    const setDrawerImageIndex = drawerPanel?.ownerImage
      ? Math.max(
          0,
          (setDrawerRow?.images || []).findIndex(
            (i) => i.order === drawerPanel.ownerImage.order
          )
        )
      : 0;

    const openPanelDrawer = (_, i) => {
      setSetDetailPanelIdx(i);
      setSetDetailDrawerOpen(true);
      const targetPage = Math.floor(i / 12) + 1;
      if (targetPage !== setDetailPage) setSetDetailPage(targetPage);
    };
    const closePanelDrawer = () => setSetDetailDrawerOpen(false);
    const prevPanel = () => {
      const cur = setDetailPanelIdx ?? 0;
      const ni = Math.max(0, cur - 1);
      setSetDetailPanelIdx(ni);
      const tp = Math.floor(ni / 12) + 1;
      if (tp !== setDetailPage) setSetDetailPage(tp);
    };
    const nextPanel = () => {
      const cur = setDetailPanelIdx ?? -1;
      const ni = Math.min(detailPanels.length - 1, cur + 1);
      setSetDetailPanelIdx(ni);
      const tp = Math.floor(ni / 12) + 1;
      if (tp !== setDetailPage) setSetDetailPage(tp);
    };

    const focusedSubLabel =
      setDetailDrawerOpen && setDetailPanelIdx != null
        ? `panel ${String(setDetailPanelIdx + 1).padStart(2, "0")}`
        : null;

    return (
      <div
        data-testid="archive-set-detail"
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
          data-testid="archive-set-detail-scroll"
          style={{
            flex: 1,
            overflowY: "auto",
            paddingRight: setDetailDrawerOpen ? drawerWidth : 0,
            transition: "padding-right 360ms cubic-bezier(.22,.85,.22,1)",
            willChange: "padding-right",
          }}
        >
          <div style={{ padding: "32px 56px 60px" }}>
            <ArchiveSetDetail
              id={firstRow?.seq_no}
              model={firstRow?.model_display_name || firstRow?.model}
              age={`${relativeAge(firstRow?.updated_at)} ago`}
              prompt={firstRow?.prompt || ""}
              panelCount={detailPanels.length}
              panels={detailPanels}
              focusedIndex={setDetailPanelIdx}
              focusedSub={focusedSubLabel}
              onPanelClick={openPanelDrawer}
              page={setDetailPage}
              pageSize={12}
              onPageChange={(p) => setSetDetailPage(p)}
              onBack={() => setSetDetailId(null)}
            />
            <div style={{ marginTop: 24 }}>
              <button
                data-testid="set-detail-close"
                onClick={closeSetDetail}
                style={{
                  padding: "8px 14px",
                  border: "1px solid var(--ink)",
                  background: "var(--card, #fffdf7)",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                }}
              >
                close set
              </button>
            </div>
          </div>
        </div>
        <JobDrawer
          row={setDrawerRow}
          imageIndex={setDrawerImageIndex}
          onClose={closePanelDrawer}
          onPrev={prevPanel}
          onNext={nextPanel}
          onOpenLightbox={() => setLightboxOpen(true)}
          width={drawerWidth}
        />

        <Lightbox
          open={lightboxOpen && !!setDrawerRow}
          row={setDrawerRow}
          imageIndex={setDrawerImageIndex}
          position={setDetailPanelIdx != null ? setDetailPanelIdx + 1 : null}
          total={detailPanels.length || null}
          onClose={() => setLightboxOpen(false)}
          onPrev={prevPanel}
          onNext={nextPanel}
        />
      </div>
    );
  }

  const showEmpty = !!userId && allRows.length === 0;
  const showNoMatches =
    !!userId && allRows.length > 0 && sortedItems.length === 0;
  const matchesCount = sortedItems.length;
  const allCount = allRows.length;
  const totalChips = chipCountFn(applied) + (sessionFilter ? 1 : 0);

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
        ref={scrollerRef}
        data-testid="archive-scroller"
        style={{
          flex: 1,
          overflowY: "auto",
          paddingRight: drawerOpen ? drawerWidth : 0,
          transition: "padding-right 360ms cubic-bezier(.22,.85,.22,1)",
          willChange: "padding-right",
        }}
      >
        <div
          ref={stageRef}
          data-testid="archive-stage"
          style={{ padding: "32px 56px 60px" }}
        >
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
              {showEmpty
                ? <>Nothing in the archive <span style={{ fontStyle: "italic" }}>yet.</span></>
                : "Everything you've made."
              }
            </h1>
          </div>

          <ResumeBanner userId={userId} />

          {/* Toolbar */}
          <div
            data-testid="archive-toolbar"
            style={{
              marginTop: 14,
              display: "flex",
              alignItems: "center",
              gap: 12,
              flexWrap: "nowrap",
              minHeight: 40,
              minWidth: 0,
            }}
          >
            {/* Trigger lives outside the scrollable strip so its
                top-right notification badge is never clipped. */}
            <div ref={filterTriggerRef} style={{ position: "relative", flexShrink: 0 }}>
              <FilterTrigger
                count={totalChips}
                onClick={() => setFilterOpen((o) => !o)}
              />
              {filterOpen && (
                <FilterPopover
                  applied={applied}
                  allRows={allRows}
                  sessionLockedByUrl={sessionLockedByUrl}
                  onApply={(next) => {
                    setApplied(next);
                    setFilterOpen(false);
                  }}
                  onClose={() => setFilterOpen(false)}
                />
              )}
            </div>
            <ChipStrip
              applied={applied}
              dynamic={dynamicOptions}
              onChange={(next) => setApplied(next)}
            />

            <div
              data-testid="archive-matches"
              style={{
                display: "flex", alignItems: "baseline", gap: 8,
                flexShrink: 0, whiteSpace: "nowrap",
              }}
            >
              <span className="ticker" style={{ fontSize: 22, fontWeight: 900, letterSpacing: "-0.03em" }}>
                {matchesCount}
              </span>
              <span className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
                matches
              </span>
              {totalChips === 0 && allCount > 0 && (
                <span style={{
                  fontFamily: "var(--font-display)",
                  fontStyle: "italic", fontSize: 14, color: "var(--ink-3)",
                }}>
                  all tasks
                </span>
              )}
            </div>

            <div ref={sortTriggerRef} style={{ position: "relative", flexShrink: 0 }}>
              <button
                data-testid="archive-sort-trigger"
                onClick={() => setSortOpen((o) => !o)}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "6px 12px", height: 32,
                  background: "var(--card, #fffdf7)",
                  border: "1px solid var(--ink)",
                  cursor: "pointer", fontFamily: "var(--font-mono)",
                  fontSize: 11, fontWeight: 600,
                }}
              >
                sort: {sortKeyLabel(sortKey)} ▼
              </button>
              {sortOpen && (
                <SortDropdown
                  current={sortKey}
                  triggerRef={sortTriggerRef}
                  onPick={(k) => {
                    setSortKey(k);
                    setSortOpen(false);
                  }}
                  onRefresh={() => {
                    void archiveStore.sync();
                    setSortOpen(false);
                  }}
                  onClose={() => setSortOpen(false)}
                />
              )}
            </div>

            {!showEmpty && totalPages > 0 && (
              <PaginationPager
                current={viewportPage}
                total={totalPages}
                onPrev={() => jumpTo(viewportPage - 1)}
                onNext={() => jumpTo(viewportPage + 1)}
              />
            )}
          </div>

          {showEmpty && <ArchiveEmptyHero />}

          {showNoMatches && (
            <ArchiveNoMatches onClear={() => setApplied(EMPTY_FILTER)} />
          )}

          {!showEmpty && !showNoMatches && (
            <div style={{ marginTop: 12 }}>
              {visibleByPage.map(({ page, items: pageItems }) => (
                <PageSection
                  key={page}
                  page={page}
                  totalPages={totalPages}
                  items={pageItems}
                  drawerHash={drawerHash}
                  blobByUrl={blobByUrl}
                  onItemClick={onItemClick}
                  rowsByHashId={rowsByHashId}
                  onNavigateToParent={(hashId, order) => {
                    navigate(`/edit/${hashId}/${order || 1}`);
                  }}
                />
              ))}
              {(() => {
                const sortedLoaded = [...loadedPages].sort((a, b) => a - b);
                const lastLoaded = sortedLoaded[sortedLoaded.length - 1] || 1;
                const showMembrane = lastLoaded < totalPages;
                if (showMembrane) {
                  return (
                    <PullMembrane
                      pull={pull}
                      isCommitting={isCommitting}
                      nextPage={lastLoaded + 1}
                      visible={true}
                    />
                  );
                }
                return (
                  <div
                    data-testid="archive-end"
                    style={{
                      padding: "40px 0",
                      textAlign: "center",
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: "var(--ink-3)",
                      letterSpacing: "0.18em",
                    }}
                  >
                    — end —
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      </div>

      <JobDrawer
        row={drawerRow}
        onClose={close}
        onPrev={prev}
        onNext={next}
        onOpenLightbox={() => setLightboxOpen(true)}
        width={drawerWidth}
      />

      <Lightbox
        open={lightboxOpen && !!drawerRow}
        row={drawerRow}
        position={idx >= 0 ? idx + 1 : null}
        total={visibleSingles.length || null}
        onClose={() => setLightboxOpen(false)}
        onPrev={prev}
        onNext={next}
      />
    </div>
  );
}

function renderSourceBadge(desc, onNavigateToParent) {
  if (!desc) return null;
  if (desc.kind === "mixed") {
    return (
      <MixedSourceBadge
        distinctCount={desc.distinctCount}
        members={desc.members}
        onPick={(m) => {
          if (m.parentHashId) onNavigateToParent?.(m.parentHashId, m.parentOrder || 1);
        }}
      />
    );
  }
  return (
    <SourceBadge
      parentSeqNo={desc.parentSeqNo}
      parentOrder={desc.parentOrder}
      derivationKind={desc.derivationKind}
      unknownOrder={desc.unknownOrder}
      onClick={() => {
        if (desc.parentHashId) onNavigateToParent?.(desc.parentHashId, desc.parentOrder || 1);
      }}
    />
  );
}

function sortKeyLabel(key) {
  switch (key) {
    case "oldest": return "oldest first";
    case "starred-first": return "starred first";
    case "set-size": return "largest set";
    case "newest":
    default: return "newest first";
  }
}

// One <section data-page="N"> for each loaded page.
function PageSection({
  page,
  totalPages,
  items,
  drawerHash,
  blobByUrl,
  onItemClick,
  rowsByHashId,
  onNavigateToParent,
}) {
  return (
    <section
      data-page={String(page)}
      style={{
        padding: page === 1 ? "0 0 14px" : "14px 0",
        borderTop: page > 1 ? "1px solid var(--rule-2)" : "none",
      }}
    >
      <div
        className="mono caps"
        style={{
          fontSize: 9,
          color: "var(--ink-3)",
          letterSpacing: "0.20em",
          marginBottom: 6,
        }}
      >
        PAGE {page} / {totalPages}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
          gap: 12,
        }}
      >
        {items.map((item) => {
          const sourceBadgeDesc = computeSourceBadge(item, rowsByHashId);
          const sourceBadge = renderSourceBadge(sourceBadgeDesc, onNavigateToParent);
          if (item.kind === "set") {
            // Expand each member into one cell per expected image so the
            // SET badge total and per-cell state stay correct even when
            // members are still RUNNING / FAILED / blob-pending.
            const cells = [];
            for (const member of item.members) {
              const expected = expectedImagesForMember(member);
              if (member.status === "FAILED" || member.status === "CANCELLED") {
                for (let k = 0; k < expected; k++) cells.push({ state: "fail" });
                continue;
              }
              if (member.status === "QUEUED" || member.status === "RUNNING") {
                for (let k = 0; k < expected; k++) cells.push({ state: "running" });
                continue;
              }
              const imgs = member.images || [];
              if (imgs.length === 0) {
                for (let k = 0; k < expected; k++) cells.push({ state: "loading" });
                continue;
              }
              for (const img of imgs) {
                const apiUrl = imageThumbUrl(member.hash_id, img.order);
                const blob = blobByUrl[apiUrl];
                cells.push(
                  blob
                    ? { src: blob, state: "done" }
                    : { state: "loading" }
                );
              }
              // Mirror the set-detail loop: when fewer images came back
              // than ``params.n`` requested, the missing slots render as
              // partial-fail so the SET badge keeps the requested total.
              for (let k = imgs.length; k < expected; k++) {
                cells.push({ state: "fail" });
              }
            }
            const stillRunning = item.members.some(
              (r) => r.status === "QUEUED" || r.status === "RUNNING"
            );
            const newest = item.members.reduce((acc, r) => {
              const t = Date.parse(r.updated_at || 0) || 0;
              return t > acc.t ? { row: r, t } : acc;
            }, { row: item.members[0], t: 0 });
            const expectedTotal = cells.length || item.members.length;
            return (
              <div
                data-testid={`card-set-${item.set_id}`}
                key={item.set_id}
              >
                <ArchiveSetCard
                  size="grid"
                  id={newest.row.seq_no}
                  model={shortModel(newest.row.model)}
                  age={relativeAge(newest.row.updated_at)}
                  images={cells}
                  totalCount={expectedTotal}
                  running={stillRunning}
                  onClick={() => onItemClick(item)}
                  sourceBadge={sourceBadge}
                />
              </div>
            );
          }

          const row = item.row;
          const focused = drawerHash === row.hash_id;
          const focusClass = focused ? "arch-focused" : "";

          if (row.status === "RUNNING") {
            const anchor =
              row._runStartedAt ||
              (row.timing?.started_at && Date.parse(row.timing.started_at)) ||
              0;
            const elapsed = anchor
              ? Math.max(0, Math.floor((Date.now() - anchor) / 1000))
              : 0;
            return (
              <div
                key={row.hash_id}
                data-testid={`card-single-${row.hash_id}`}
                data-hash={row.hash_id}
                data-status={row.status}
                data-seq={row.seq_no}
                data-starred="false"
                data-session={row.session?.id || row.session_id || ""}
              >
                <RunningCard
                  size="grid"
                  id={row.seq_no}
                  model={shortModel(row.model)}
                  ratio={aspectFromImage(row.images?.[0])}
                  seconds={elapsed}
                  onClick={() => onItemClick(item)}
                  className={focusClass}
                />
              </div>
            );
          }

          if (row.status === "QUEUED") {
            return (
              <div
                key={row.hash_id}
                data-testid={`card-single-${row.hash_id}`}
                data-hash={row.hash_id}
                data-status={row.status}
                data-seq={row.seq_no}
                data-starred="false"
                data-session={row.session?.id || row.session_id || ""}
              >
                <QueuedCard
                  size="grid"
                  id={row.seq_no}
                  model={shortModel(row.model)}
                  ratio={aspectFromImage(row.images?.[0])}
                  position={row._position || 1}
                  eta={row._eta != null ? `~ ${row._eta}s` : undefined}
                  onClick={() => onItemClick(item)}
                  className={focusClass}
                />
              </div>
            );
          }

          if (row.status === "FAILED") {
            return (
              <div
                key={row.hash_id}
                data-testid={`card-single-${row.hash_id}`}
                data-hash={row.hash_id}
                data-status={row.status}
                data-seq={row.seq_no}
                data-starred="false"
                data-session={row.session?.id || row.session_id || ""}
              >
                <FailCard
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
              </div>
            );
          }

          if (row.status === "CANCELLED") {
            return (
              <div
                key={row.hash_id}
                data-testid={`card-single-${row.hash_id}`}
                data-hash={row.hash_id}
                data-status={row.status}
                data-seq={row.seq_no}
                data-starred="false"
                data-session={row.session?.id || row.session_id || ""}
              >
                <FailCard
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
              </div>
            );
          }

          return (
            <SingleImageCard
              key={row.hash_id}
              row={row}
              focused={focused}
              onClick={() => onItemClick(item)}
              sourceBadge={sourceBadge}
            />
          );
        })}
      </div>
    </section>
  );
}
