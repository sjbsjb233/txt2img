// Archive view preferences — filter + sort persistence.
//
// Two independent slices:
//   - filter (period / model / shape / status / starred / session)
//   - sort   (newest | oldest | starred-first | set-size)
//
// Both persist to localStorage with the v1 keys agreed in the PRD; both
// expose a subscribe hook so cross-component updates stay in sync.
//
// session filter is intentionally NOT stored here — it lives in the URL
// search param `session_id`. ArchivePage merges the two at read time.

import { useEffect, useState } from "react";

export const FILTER_KEY = "archive.filter.v1";
export const SORT_KEY = "archive.sort.v1";

export const PERIOD_VALUES = [null, "7d", "30d", "90d"];
export const STATUS_VALUES = [
  "SUCCEEDED",
  "FAILED",
  "RUNNING",
  "QUEUED",
  "CANCELLED",
];
export const SORT_KEYS = ["newest", "oldest", "starred-first", "set-size"];

export const EMPTY_FILTER = Object.freeze({
  period: null,           // null | "7d" | "30d" | "90d"
  model: [],              // string[] — short model names (matches shortModel())
  shape: [],              // string[] — aspect labels
  status: [],             // string[] — STATUS_VALUES subset
  starred: false,         // boolean
  session: null,          // string|null — session_id (URL takes precedence)
});

function clone(v) {
  return JSON.parse(JSON.stringify(v));
}

function isFilterEmpty(f) {
  if (!f) return true;
  if (f.period) return false;
  if (f.model && f.model.length) return false;
  if (f.shape && f.shape.length) return false;
  if (f.status && f.status.length) return false;
  if (f.starred) return false;
  if (f.session) return false;
  return true;
}

function sanitizeFilter(raw) {
  const out = clone(EMPTY_FILTER);
  if (!raw || typeof raw !== "object") return out;
  if (PERIOD_VALUES.includes(raw.period)) out.period = raw.period;
  if (Array.isArray(raw.model)) out.model = raw.model.filter((v) => typeof v === "string");
  if (Array.isArray(raw.shape)) out.shape = raw.shape.filter((v) => typeof v === "string");
  if (Array.isArray(raw.status)) {
    out.status = raw.status.filter((v) => STATUS_VALUES.includes(v));
  }
  if (typeof raw.starred === "boolean") out.starred = raw.starred;
  if (typeof raw.session === "string" && raw.session) out.session = raw.session;
  return out;
}

function sanitizeSort(raw) {
  return SORT_KEYS.includes(raw) ? raw : "newest";
}

// ---------------------------------------------------------------------
// Module-level state, shared across hook instances
// ---------------------------------------------------------------------

function readFilter() {
  try {
    const raw = localStorage.getItem(FILTER_KEY);
    if (!raw) return clone(EMPTY_FILTER);
    return sanitizeFilter(JSON.parse(raw));
  } catch {
    return clone(EMPTY_FILTER);
  }
}

function readSort() {
  try {
    const raw = localStorage.getItem(SORT_KEY);
    if (!raw) return "newest";
    return sanitizeSort(JSON.parse(raw));
  } catch {
    return "newest";
  }
}

function writeFilter(value) {
  try {
    localStorage.setItem(FILTER_KEY, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

function writeSort(value) {
  try {
    localStorage.setItem(SORT_KEY, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

let _filter = readFilter();
let _sort = readSort();
const filterListeners = new Set();
const sortListeners = new Set();

function notifyFilters() {
  for (const fn of filterListeners) {
    try { fn(); } catch { /* swallow */ }
  }
}
function notifySorts() {
  for (const fn of sortListeners) {
    try { fn(); } catch { /* swallow */ }
  }
}

export function subscribeFilters(fn) {
  filterListeners.add(fn);
  return () => filterListeners.delete(fn);
}
export function subscribeSort(fn) {
  sortListeners.add(fn);
  return () => sortListeners.delete(fn);
}

export function getFilter() {
  return _filter;
}
export function setFilter(next) {
  _filter = sanitizeFilter(next);
  writeFilter(_filter);
  notifyFilters();
}
export function getSort() {
  return _sort;
}
export function setSort(next) {
  _sort = sanitizeSort(next);
  writeSort(_sort);
  notifySorts();
}

export function clearFilter() {
  setFilter(EMPTY_FILTER);
}

export { isFilterEmpty };

// ---------------------------------------------------------------------
// React hooks
// ---------------------------------------------------------------------

export function useArchiveFilters() {
  const [, tick] = useState(0);
  useEffect(() => subscribeFilters(() => tick((n) => n + 1)), []);
  return { applied: _filter, setApplied: setFilter, clear: clearFilter };
}

export function useArchiveSort() {
  const [, tick] = useState(0);
  useEffect(() => subscribeSort(() => tick((n) => n + 1)), []);
  return [_sort, setSort];
}
