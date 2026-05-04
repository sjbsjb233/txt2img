// Pagination hook for the archive grid.
//
// Two interaction modes that share state:
//   - pull-to-load: scrolling into the tension zone past a threshold,
//     then stopping for STOP_MS, commits the next page (append).
//   - jump: clicking ‹/› closes other loaded pages and jumps to one.
//
// `loadedPages` is always either [1..k] (append mode) or [k] (post-jump
// single page). The parent controls reset() on filter/sort changes.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export const PAGE_SIZE_DEFAULT = 60;
export const TENSION_HEIGHT = 380;
const THRESHOLD_PCT = 0.6;
const THRESHOLD_MIN = 240;
const STOP_MS = 180;

export default function useArchivePagination({
  scrollerRef,
  items,
  pageSize = PAGE_SIZE_DEFAULT,
}) {
  const [loadedPages, setLoadedPages] = useState([1]);
  const [viewportPage, setViewportPage] = useState(1);
  const [pull, setPull] = useState(0);
  const [isCommitting, setIsCommitting] = useState(false);
  const stopTimer = useRef(null);
  const pendingTopOnLoad = useRef(0);
  const lastItemsLen = useRef(0);

  const totalPages = Math.max(1, Math.ceil((items?.length || 0) / pageSize));

  // Clamp loadedPages if items shrunk (e.g. SSE delete).
  useEffect(() => {
    setLoadedPages((prev) => {
      const clamped = prev.filter((p) => p <= totalPages);
      if (clamped.length === 0) return [1];
      if (clamped.length === prev.length) return prev;
      return clamped;
    });
    setViewportPage((p) => Math.min(p, totalPages));
  }, [totalPages]);

  // Visible items: union of loaded pages, in their natural order.
  const visibleByPage = useMemo(() => {
    const out = [];
    const sortedPages = [...loadedPages].sort((a, b) => a - b);
    for (const p of sortedPages) {
      const start = (p - 1) * pageSize;
      const end = Math.min(start + pageSize, items.length);
      out.push({ page: p, items: items.slice(start, end) });
    }
    return out;
  }, [items, loadedPages, pageSize]);

  const visibleItems = useMemo(
    () => visibleByPage.flatMap((b) => b.items),
    [visibleByPage]
  );

  // ----- pull-to-load mechanics -----

  const computeThreshold = useCallback(() => {
    const sc = scrollerRef.current;
    if (!sc) return THRESHOLD_MIN;
    return Math.max(THRESHOLD_MIN, sc.clientHeight * THRESHOLD_PCT);
  }, [scrollerRef]);

  const commitNextPage = useCallback(() => {
    setLoadedPages((prev) => {
      const sorted = [...prev].sort((a, b) => a - b);
      const last = sorted[sorted.length - 1] || 0;
      const next = last + 1;
      if (next > totalPages) return prev;
      return [...sorted, next];
    });
    setIsCommitting(true);
    pendingTopOnLoad.current = 1; // signal: scroll to last page top after layout
    setTimeout(() => {
      setIsCommitting(false);
      setPull(0);
    }, 520);
  }, [totalPages]);

  const onScroll = useCallback(() => {
    const sc = scrollerRef.current;
    if (!sc) return;
    if (isCommitting) return;
    const top = sc.scrollTop;
    const max = sc.scrollHeight - sc.clientHeight;
    // Update viewportPage from data-page sections.
    const anchor = top + 80;
    const sections = sc.querySelectorAll("section[data-page]");
    let detected = viewportPage;
    for (const sec of sections) {
      const off = sec.offsetTop;
      const h = sec.offsetHeight;
      if (off <= anchor && off + h > anchor) {
        detected = Number(sec.getAttribute("data-page")) || 1;
        break;
      }
    }
    if (detected !== viewportPage) setViewportPage(detected);

    // Tension progress: how far past the bottom of the loaded grid.
    // The tension zone sits below the grid sections; once `top` is
    // close to `max`, we compute pull.
    const tensionEntry = max - TENSION_HEIGHT;
    const distancePast = Math.max(0, top - tensionEntry);
    const threshold = computeThreshold();
    const newPull = Math.min(1.4, distancePast / threshold);
    setPull(newPull);

    // Reset stop timer.
    if (stopTimer.current) clearTimeout(stopTimer.current);
    stopTimer.current = setTimeout(() => {
      const sc2 = scrollerRef.current;
      if (!sc2 || isCommitting) return;
      // re-measure final pull
      const top2 = sc2.scrollTop;
      const max2 = sc2.scrollHeight - sc2.clientHeight;
      const dp = Math.max(0, top2 - (max2 - TENSION_HEIGHT));
      const finalPull = dp / computeThreshold();
      const sortedLoaded = [...loadedPages].sort((a, b) => a - b);
      const lastLoaded = sortedLoaded[sortedLoaded.length - 1] || 0;
      const canCommit = lastLoaded < totalPages;
      if (finalPull >= 1 && canCommit) {
        commitNextPage();
      } else if (finalPull > 0) {
        // snap-back: scroll to tension entry.
        try {
          sc2.scrollTo({ top: max2 - TENSION_HEIGHT, behavior: "smooth" });
        } catch {
          sc2.scrollTop = max2 - TENSION_HEIGHT;
        }
        setPull(0);
      }
    }, STOP_MS);
  }, [
    scrollerRef,
    viewportPage,
    isCommitting,
    loadedPages,
    totalPages,
    computeThreshold,
    commitNextPage,
  ]);

  useEffect(() => {
    const sc = scrollerRef.current;
    if (!sc) return undefined;
    sc.addEventListener("scroll", onScroll, { passive: true });
    return () => sc.removeEventListener("scroll", onScroll);
  }, [scrollerRef, onScroll]);

  // After a commit appends a page, scroll to its top (offsetTop − 14).
  useEffect(() => {
    if (!pendingTopOnLoad.current) return;
    const sc = scrollerRef.current;
    if (!sc) return;
    const id = requestAnimationFrame(() => {
      const sections = sc.querySelectorAll("section[data-page]");
      const last = sections[sections.length - 1];
      if (last) {
        try {
          sc.scrollTo({ top: Math.max(0, last.offsetTop - 14), behavior: "smooth" });
        } catch {
          sc.scrollTop = Math.max(0, last.offsetTop - 14);
        }
      }
      pendingTopOnLoad.current = 0;
    });
    return () => cancelAnimationFrame(id);
  }, [loadedPages, scrollerRef]);

  // jumpTo closes all other pages, scrolls to top of new page.
  const jumpTo = useCallback(
    (p) => {
      if (isCommitting) return;
      const target = Math.max(1, Math.min(totalPages, p));
      setLoadedPages([target]);
      setViewportPage(target);
      const sc = scrollerRef.current;
      if (sc) {
        sc.scrollTop = 0;
        setPull(0);
      }
    },
    [isCommitting, totalPages, scrollerRef]
  );

  // reset() — invoked by parent on filter/sort changes.
  const reset = useCallback(() => {
    setLoadedPages([1]);
    setViewportPage(1);
    setPull(0);
    setIsCommitting(false);
    const sc = scrollerRef.current;
    if (sc) sc.scrollTop = 0;
  }, [scrollerRef]);

  // Track lastItemsLen to support SSE-induced shrink without reset.
  useEffect(() => {
    lastItemsLen.current = items.length;
  }, [items.length]);

  return {
    loadedPages,
    viewportPage,
    totalPages,
    visibleByPage,
    visibleItems,
    pull,
    isCommitting,
    jumpTo,
    reset,
    pageSize,
  };
}
