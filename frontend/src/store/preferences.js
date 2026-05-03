// Preferences store — single source of truth for the user's settings.
//
// Two layers of state:
//   - `_saved`   — the canonical preferences object, last seen from the
//                  server. Mirrors the GET /api/me/preferences shape.
//   - `_dirty`   — local edits that haven't been PATCH'd yet. The Save
//                  button on the settings page diffs and ships only
//                  fields that actually changed.
//
// The store is also responsible for applying side-effects to the DOM:
// theme / density / reduce-motion / interface-scale all live on the
// document root as data-attributes / inline styles. The CSS reads them
// from tokens.css.

import { useEffect, useState } from "react";
import * as meApi from "../api/me.js";

// ---------------------------------------------------------------------------
// Defaults — used when we haven't loaded yet (logged-out, first paint, etc.)
// ---------------------------------------------------------------------------

export const DEFAULT_PREFS = Object.freeze({
  generation: {
    default_model_id: null,
    default_aspect_ratio: "1:1",
    default_batch_size: 1,
    auto_bind_session: true,
    auto_retry: true,
    remember_prompt_history: true,
  },
  notifications: {
    browser_on_complete: false,
    sound_on_complete: false,
    sound_volume: 60,
    desktop_badge: true,
    announcements_level: "all",
  },
  appearance: {
    theme: "system",
    density: "comfortable",
    sidebar_default: "expanded",
  },
  locale: {
    language: "en",
    timezone: "Asia/Shanghai",
    date_format: "iso",
  },
  privacy: {
    hide_prompts_in_screenshot_mode: false,
  },
  updated_at: null,
});

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

let _saved = clone(DEFAULT_PREFS);
let _dirty = {};
let _loaded = false;
const listeners = new Set();

const LOCAL_KEY_PREFIX = "txt2img_local_pref_";
// Some preferences are device-local rather than synced (per PRD §4.4).
// Keep them here and persist to localStorage.
export const LOCAL_PREFS = Object.freeze({
  reduce_motion: false,
  interface_scale: "A",       // 'A-' | 'A' | 'A+'
  cache_retention: "keep_all", // 'keep_all' | 'last_30d' | 'manual'
});
let _local = readLocal();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function notify() {
  for (const fn of listeners) {
    try {
      fn();
    } catch {
      /* one bad listener should not break the others */
    }
  }
}

function readLocal() {
  const out = { ...LOCAL_PREFS };
  for (const key of Object.keys(LOCAL_PREFS)) {
    try {
      const raw = localStorage.getItem(LOCAL_KEY_PREFIX + key);
      if (raw === null) continue;
      const parsed = JSON.parse(raw);
      if (parsed !== undefined && parsed !== null) {
        out[key] = parsed;
      }
    } catch {
      /* ignore corrupt entry */
    }
  }
  return out;
}

function writeLocal(key, value) {
  try {
    localStorage.setItem(LOCAL_KEY_PREFIX + key, JSON.stringify(value));
  } catch {
    /* localStorage may be disabled in private mode */
  }
}

function getByPath(obj, section, key) {
  if (!obj) return undefined;
  if (!section) return obj[key];
  return obj[section] ? obj[section][key] : undefined;
}

function deepMergeDirty(base, overrides) {
  const out = clone(base);
  for (const section of Object.keys(overrides)) {
    if (typeof overrides[section] !== "object" || overrides[section] === null) continue;
    out[section] = { ...out[section], ...overrides[section] };
  }
  return out;
}

// ---------------------------------------------------------------------------
// DOM side-effects
// ---------------------------------------------------------------------------

let _systemDarkMql = null;
let _onSystemDark = null;

export function applyTheme(theme) {
  // theme ∈ {light, dark, system}. ``system`` follows the OS preference.
  const root = document.documentElement;
  if (theme === "dark") {
    root.setAttribute("data-theme", "dark");
  } else if (theme === "light") {
    root.setAttribute("data-theme", "light");
  } else {
    if (_systemDarkMql == null) {
      _systemDarkMql = window.matchMedia("(prefers-color-scheme: dark)");
    }
    const sync = () => {
      root.setAttribute(
        "data-theme",
        _systemDarkMql.matches ? "dark" : "light"
      );
    };
    sync();
    if (_onSystemDark) {
      _systemDarkMql.removeEventListener("change", _onSystemDark);
    }
    _onSystemDark = sync;
    _systemDarkMql.addEventListener("change", sync);
    return;
  }
  if (_onSystemDark && _systemDarkMql) {
    _systemDarkMql.removeEventListener("change", _onSystemDark);
    _onSystemDark = null;
  }
}

export function applyDensity(density) {
  document.documentElement.setAttribute("data-density", density);
}

export function applyReduceMotion(on) {
  document.documentElement.setAttribute("data-reduced-motion", on ? "true" : "false");
}

export function applyInterfaceScale(scale) {
  // Token mapping → root font-size in px. Keep these conservative —
  // wider deltas wreak havoc on dense flexbox layouts elsewhere.
  const map = { "A-": 14, "A": 16, "A+": 18 };
  const px = map[scale] || 16;
  document.documentElement.style.fontSize = `${px}px`;
}

function applyAll() {
  const v = getMerged();
  applyTheme(v.appearance.theme);
  applyDensity(v.appearance.density);
  applyReduceMotion(_local.reduce_motion);
  applyInterfaceScale(_local.interface_scale);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getMerged() {
  return deepMergeDirty(_saved, _dirty);
}

export function getSaved() {
  return _saved;
}

export function getLocal() {
  return _local;
}

export function isLoaded() {
  return _loaded;
}

export function isDirty() {
  for (const section of Object.keys(_dirty)) {
    if (_dirty[section] && Object.keys(_dirty[section]).length > 0) {
      return true;
    }
  }
  return false;
}

export async function load() {
  try {
    const data = await meApi.getPreferences();
    _saved = data;
    _loaded = true;
    _dirty = {};
    applyAll();
    notify();
  } catch (err) {
    // Failure to load preferences is not fatal — the user can still
    // use the app, they'll just see system defaults until next refresh.
    // eslint-disable-next-line no-console
    console.warn("preferences load failed", err);
  }
}

/** Stage a change locally; nothing hits the server until ``save()``. */
export function setLocalDraft(section, key, value) {
  if (!_dirty[section]) _dirty[section] = {};
  // If the new value matches the saved one, drop it from dirty so the
  // Save button correctly reflects the diff.
  const saved = getByPath(_saved, section, key);
  if (Object.is(saved, value)) {
    delete _dirty[section][key];
    if (Object.keys(_dirty[section]).length === 0) delete _dirty[section];
  } else {
    _dirty[section][key] = value;
  }
  // Apply visual side-effects immediately so the user can preview their
  // change without saving (they can still revert via "Reset").
  if (section === "appearance") {
    if (key === "theme") applyTheme(value);
    if (key === "density") applyDensity(value);
  }
  notify();
}

/** Discard staged changes. */
export function reset() {
  _dirty = {};
  applyAll();
  notify();
}

/** PATCH the staged diff and merge the response back into _saved. */
export async function save() {
  if (!isDirty()) return;
  const diff = clone(_dirty);
  const next = await meApi.patchPreferences(diff);
  _saved = next;
  _dirty = {};
  applyAll();
  notify();
}

// ----- local-only preferences -----

export function setLocal(key, value) {
  if (!Object.prototype.hasOwnProperty.call(LOCAL_PREFS, key)) return;
  _local = { ..._local, [key]: value };
  writeLocal(key, value);
  if (key === "reduce_motion") applyReduceMotion(Boolean(value));
  if (key === "interface_scale") applyInterfaceScale(value);
  notify();
}

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

export function usePreferences() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return {
    prefs: getMerged(),
    saved: _saved,
    local: _local,
    loaded: _loaded,
    dirty: isDirty(),
  };
}

// Apply local-only (cached) attributes immediately on import so the
// first paint already respects reduce-motion / interface scale even
// before /api/me/preferences resolves.
applyReduceMotion(_local.reduce_motion);
applyInterfaceScale(_local.interface_scale);
