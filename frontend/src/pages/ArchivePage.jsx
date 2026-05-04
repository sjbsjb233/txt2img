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
import { useSearchParams } from "react-router-dom";
import Icon from "../components/Icon.jsx";
import {
  RunningCard,
  QueuedCard,
  FailCard,
  ArchiveSetCard,
  ArchiveSetDetail,
  ArchiveEmptyHero,
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
} from "../components/archive/archiveFilter.js";
import FilterPopover from "../components/archive/FilterPopover.jsx";
import SortDropdown from "../components/archive/SortDropdown.jsx";
import ChipStrip from "../components/archive/ChipStrip.jsx";
import PaginationPager from "../components/archive/PaginationPager.jsx";
import PullMembrane from "../components/archive/PullMembrane.jsx";
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

function SingleImageCard({ row, focused, onClick }) {
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
// Right-side detail drawer
// ---------------------------------------------------------------------------

function JobDrawer({ row, onClose, onPrev, onNext, width }) {
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
  const img = it.images?.[0];
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
        <button
          data-testid="drawer-close"
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
              data-testid="drawer-star"
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
            <MetaRow k="session" v={sessionLabel} />
          </div>
        </div>
      </div>
    </aside>
  );
}

function MetaRow({ k, v }) {
  return (
    <div
      style={{
        display: "flex", justifyContent: "space-between",
        padding: "6px 0", borderBottom: "1px dashed var(--rule-2)",
      }}
    >
      <span style={{ color: "var(--ink-3)" }}>{k}</span>
      <span style={{ fontWeight: 600, color: "var(--ink)" }}>{v}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ArchivePage() {
  const { user } = useAuth();
  const userId = user?.id || null;

  useEffect(() => {
    if (!userId) return;
    void archiveStore.mount(userId);
  }, [userId]);

  const allRows = archiveStore.useArchive();
  const { applied, setApplied } = useArchiveFilters();
  const [sortKey, setSortKey] = useArchiveSort();

  const [searchParams] = useSearchParams();
  const sessionFilter = searchParams.get("session_id");
  const sessionLockedByUrl = !!sessionFilter;

  const [filterOpen, setFilterOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [drawerHash, setDrawerHash] = useState(null);
  const [setDetailId, setSetDetailId] = useState(null);

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

  // Pre-fetch blob URLs for visible items only.
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
    return out;
  }, [visibleItems]);
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

  const close = () => setDrawerHash(null);
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

  // SET full-page detail
  if (setDetailId) {
    const setItem = sortedItems.find(
      (it) => it.kind === "set" && it.set_id === setDetailId
    );
    if (!setItem) {
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
        data-testid="archive-set-detail"
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
          onBack={() => {
            // Cleanup the URL session detail id; users return via close button.
            setSetDetailId(null);
          }}
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

          {/* Toolbar */}
          <div
            data-testid="archive-toolbar"
            style={{
              marginTop: 14,
              display: "flex",
              alignItems: "center",
              gap: 14,
              flexWrap: "nowrap",
              minHeight: 40,
              minWidth: 0,
            }}
          >
            <div
              ref={filterTriggerRef}
              style={{
                position: "relative",
                flex: "1 1 0",
                minWidth: 0,
                display: "flex",
                alignItems: "center",
              }}
            >
              <ChipStrip
                applied={applied}
                dynamic={dynamicOptions}
                chipCount={totalChips}
                onChange={(next) => setApplied(next)}
                onAddFilter={() => setFilterOpen((o) => !o)}
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
            <div
              data-testid="archive-no-matches"
              style={{
                marginTop: 60,
                padding: "40px 20px",
                textAlign: "center",
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                color: "var(--ink-3)",
                border: "1px dashed var(--ink-3)",
              }}
            >
              No items match your filters.
              <br />
              <button
                onClick={() => setApplied(EMPTY_FILTER)}
                style={{
                  marginTop: 12,
                  background: "transparent",
                  border: "1px solid var(--ink)",
                  padding: "6px 12px",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--ink)",
                }}
              >
                clear filters
              </button>
            </div>
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
        width={drawerWidth}
      />
    </div>
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
function PageSection({ page, totalPages, items, drawerHash, blobByUrl, onItemClick }) {
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
          if (item.kind === "set") {
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
              <div
                data-testid={`card-set-${item.set_id}`}
                key={item.set_id}
              >
                <ArchiveSetCard
                  size="grid"
                  id={newest.row.seq_no}
                  model={shortModel(newest.row.model)}
                  age={relativeAge(newest.row.updated_at)}
                  images={images.length > 0 ? images : item.members.map(() => ({}))}
                  totalCount={images.length || item.members.length}
                  running={stillRunning}
                  onClick={() => onItemClick(item)}
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
            />
          );
        })}
      </div>
    </section>
  );
}
