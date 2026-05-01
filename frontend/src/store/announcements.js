// Announcements store — keeps the list of currently-active announcements
// in memory, hydrates from /api/announcements/active on login, and
// listens to the SSE ``announcement`` event for real-time additions.
//
// Lifecycle mirrors store/archive.js: ``mount(userId)`` is called from
// App.jsx after login, ``unmount()`` clears state on logout. Multiple
// mounts for the same user are no-ops; switching users wipes the
// cache.
//
// Read-tracking strategy:
//   - When the user dismisses a banner, we remove it from the in-memory
//     list AND POST /announcements/<id>/read to the backend so it never
//     comes back via the SSE replay or a future /active call.
//   - We don't keep a local "already-read" set — the backend is the
//     source of truth via the AnnouncementRead row. If a network blip
//     drops the POST, the next /active call will re-surface the banner;
//     dismissing again is harmless.

import { useEffect, useState } from "react";

import {
  getActiveAnnouncements,
  markRead as apiMarkRead,
} from "../api/announcements.js";
import * as sseStore from "./sse.js";

// ---------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------

let activeUserId = null;
const items = new Map(); // id -> announcement payload (user-facing shape)
const subscribers = new Set();
const sseUnsubs = new Set();

// ---------------------------------------------------------------------
// Pub/sub
// ---------------------------------------------------------------------

function notify() {
  for (const fn of subscribers) {
    try {
      fn();
    } catch {
      // Bad listener should not bring down the rest.
    }
  }
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

/**
 * React hook: re-renders whenever the announcement list changes.
 * Returns the sorted-by-priority list — banner UI binds straight to it.
 */
export function useAnnouncements() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return getList();
}

/** Read-only snapshot of the current list, sorted highest-priority first. */
export function getList() {
  const list = Array.from(items.values());
  list.sort((a, b) => {
    const pa = (a?.priority ?? 0) - (b?.priority ?? 0);
    if (pa !== 0) return -pa;
    const ta = a?.starts_at || "";
    const tb = b?.starts_at || "";
    return tb.localeCompare(ta);
  });
  return list;
}

// ---------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------

/**
 * Bind the store to a user. Hydrates from /active and starts listening
 * to SSE. Idempotent for the same user; switching users wipes state.
 */
export async function mount(userId) {
  if (!userId) {
    unmount();
    return;
  }
  if (activeUserId !== userId) {
    items.clear();
    activeUserId = userId;
  }

  await refresh();

  // Replace any prior subscriptions before re-subscribing — re-mounting
  // for the same user (e.g. token refresh) would otherwise leak handlers.
  for (const off of sseUnsubs) off();
  sseUnsubs.clear();

  sseUnsubs.add(
    sseStore.subscribe("announcement", (payload) => {
      if (!payload?.id) return;
      items.set(payload.id, payload);
      notify();
    }),
  );
}

/** Tear down without clearing in-memory cache. */
export function unmount() {
  for (const off of sseUnsubs) off();
  sseUnsubs.clear();
  // Intentionally keep ``items`` so a fast logout/login cycle for the
  // same user doesn't flicker. The next ``mount`` re-hydrates from the
  // server anyway.
}

// ---------------------------------------------------------------------
// Operations
// ---------------------------------------------------------------------

/** Re-pull the active list from the backend. Safe to call any time. */
export async function refresh() {
  try {
    const data = await getActiveAnnouncements();
    items.clear();
    for (const it of data?.items || []) {
      if (it?.id) items.set(it.id, it);
    }
    notify();
  } catch (err) {
    if (!err?.silent) {
      // eslint-disable-next-line no-console
      console.warn("announcements: refresh failed", err);
    }
  }
}

/**
 * Dismiss one announcement: optimistically drop it from local state and
 * POST the read marker. If the POST fails we don't put the banner back
 * — the next page-load will re-fetch the active list and the banner will
 * naturally re-appear if the dismiss never landed.
 */
export async function dismiss(id) {
  if (!items.has(id)) return;
  items.delete(id);
  notify();
  try {
    await apiMarkRead(id);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("announcements: mark-read failed", err);
  }
}
