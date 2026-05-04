// Pure helpers for the archive filter pipeline.
//
// Kept out of the React tree so they're easy to unit-test and reuse
// from anywhere (e.g. the dev-only console hooks).

export function shortModel(modelId) {
  if (!modelId) return "";
  if (modelId === "gpt-image-2" || modelId === "gpt-image-2-2026-04-21")
    return "gpt-2";
  if (modelId === "gemini-3-pro-image-preview") return "pro";
  if (modelId === "gemini-3.1-flash-image-preview") return "flash";
  if (modelId.startsWith("gemini-")) return "gemini";
  return modelId.slice(0, 12);
}

export function aspectFromImage(img) {
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
  return r >= 1 ? `${r.toFixed(2)}:1` : `1:${(1 / r).toFixed(2)}`;
}

export function rowAspect(row) {
  return aspectFromImage(row?.images?.[0]);
}

export function rowSession(row) {
  // SSE-injected rows may carry session_id but no session object.
  return row?.session?.id || row?.session_id || null;
}

export function rowSessionName(row) {
  return row?.session?.name || (row?.session_id ? row.session_id : "");
}

export function cutoffForPeriod(period) {
  const now = Date.now();
  switch (period) {
    case "7d":
      return now - 7 * 86400_000;
    case "30d":
      return now - 30 * 86400_000;
    case "90d":
      return now - 90 * 86400_000;
    default:
      return 0;
  }
}

// Apply the AND of every active filter dimension to a single row.
export function rowMatches(row, applied) {
  if (!row) return false;
  if (!applied) return true;
  const cutoff = cutoffForPeriod(applied.period);
  if (cutoff > 0) {
    const t = Date.parse(row.updated_at || 0) || 0;
    if (t < cutoff) return false;
  }
  if (applied.model && applied.model.length) {
    if (!applied.model.includes(shortModel(row.model))) return false;
  }
  if (applied.shape && applied.shape.length) {
    if (!applied.shape.includes(rowAspect(row))) return false;
  }
  if (applied.status && applied.status.length) {
    if (!applied.status.includes(row.status)) return false;
  }
  if (applied.starred) {
    const has = (row.images || []).some((img) => img.starred);
    if (!has) return false;
  }
  if (applied.session) {
    if (rowSession(row) !== applied.session) return false;
  }
  return true;
}

export function filterRows(allRows, applied) {
  if (!Array.isArray(allRows)) return [];
  return allRows.filter((r) => rowMatches(r, applied));
}

// SET semantics: a row that survived filterRows() carries the SET; we
// only render the rows that survived. Empty SETs are dropped here.
export function buildItems(rows) {
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
    if (members.length === 0) continue;
    members.sort((a, b) => (a.updated_at || "").localeCompare(b.updated_at || ""));
    items.push({ kind: "set", set_id: setId, members });
  }
  return items;
}

export function itemUpdatedAt(item) {
  if (item.kind === "set") {
    let max = 0;
    for (const m of item.members) {
      const t = Date.parse(m.updated_at || 0) || 0;
      if (t > max) max = t;
    }
    return max;
  }
  return Date.parse(item.row?.updated_at || 0) || 0;
}

export function itemMinUpdatedAt(item) {
  if (item.kind === "set") {
    let min = Infinity;
    for (const m of item.members) {
      const t = Date.parse(m.updated_at || 0) || 0;
      if (t < min) min = t;
    }
    return min === Infinity ? 0 : min;
  }
  return Date.parse(item.row?.updated_at || 0) || 0;
}

export function itemHasStarred(item) {
  if (item.kind === "set") {
    for (const m of item.members) {
      if ((m.images || []).some((img) => img.starred)) return true;
    }
    return false;
  }
  return (item.row.images || []).some((img) => img.starred);
}

export function itemSize(item) {
  return item.kind === "set" ? item.members.length : 1;
}

export function sortItems(items, sortKey) {
  const out = items.slice();
  switch (sortKey) {
    case "oldest":
      out.sort((a, b) => itemMinUpdatedAt(a) - itemMinUpdatedAt(b));
      return out;
    case "starred-first":
      out.sort((a, b) => {
        const sa = itemHasStarred(a) ? 1 : 0;
        const sb = itemHasStarred(b) ? 1 : 0;
        if (sa !== sb) return sb - sa;
        return itemUpdatedAt(b) - itemUpdatedAt(a);
      });
      return out;
    case "set-size":
      out.sort((a, b) => {
        const diff = itemSize(b) - itemSize(a);
        if (diff !== 0) return diff;
        return itemUpdatedAt(b) - itemUpdatedAt(a);
      });
      return out;
    case "newest":
    default:
      out.sort((a, b) => itemUpdatedAt(b) - itemUpdatedAt(a));
      return out;
  }
}

// Compute dynamic option lists for the filter popover from the current
// row population. Returns objects keyed by id.
export function computeFilterOptions(allRows) {
  const models = new Set();
  const shapes = new Set();
  const sessionMap = new Map(); // id -> name
  for (const row of allRows || []) {
    const m = shortModel(row.model);
    if (m) models.add(m);
    const a = rowAspect(row);
    if (a) shapes.add(a);
    const sid = rowSession(row);
    if (sid && !sessionMap.has(sid)) {
      sessionMap.set(sid, rowSessionName(row));
    }
  }
  return {
    model: [...models].sort(),
    shape: [...shapes].sort(),
    session: [...sessionMap.entries()]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => (a.name || "").localeCompare(b.name || "")),
  };
}

export const PROPERTY_DEFS = [
  { id: "period", label: "Period", kind: "single" },
  { id: "model", label: "Model", kind: "multi" },
  { id: "shape", label: "Shape", kind: "multi" },
  { id: "status", label: "Status", kind: "multi" },
  { id: "starred", label: "Starred only", kind: "toggle" },
  { id: "session", label: "Session", kind: "single" },
];

// True if the property currently carries any value in `applied`.
export function propertyHasValue(applied, propId) {
  if (!applied) return false;
  const v = applied[propId];
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "boolean") return v;
  return v !== null && v !== undefined && v !== "";
}

// Format a chip label given (propId, value).
export function chipLabel(propId, value, dynamic) {
  switch (propId) {
    case "period":
      if (value === "7d") return "period: last 7 days";
      if (value === "30d") return "period: last 30 days";
      if (value === "90d") return "period: last 90 days";
      return "period: any time";
    case "model":
      return `model: ${value}`;
    case "shape":
      return `shape: ${value}`;
    case "status":
      return `status: ${String(value).toLowerCase()}`;
    case "starred":
      return "★ starred";
    case "session": {
      const item = (dynamic?.session || []).find((s) => s.id === value);
      return `session: ${item?.name || value}`;
    }
    default:
      return `${propId}: ${value}`;
  }
}

// Iterate over every applied chip-pair as { propId, value }.
export function forEachChip(applied, cb) {
  if (!applied) return;
  if (applied.period) cb({ propId: "period", value: applied.period });
  for (const m of applied.model || []) cb({ propId: "model", value: m });
  for (const s of applied.shape || []) cb({ propId: "shape", value: s });
  for (const s of applied.status || []) cb({ propId: "status", value: s });
  if (applied.starred) cb({ propId: "starred", value: true });
  if (applied.session) cb({ propId: "session", value: applied.session });
}

export function chipCount(applied) {
  let n = 0;
  forEachChip(applied, () => { n += 1; });
  return n;
}

// Remove a single chip from `applied` and return the resulting filter.
export function removeChip(applied, propId, value) {
  const next = JSON.parse(JSON.stringify(applied || {}));
  if (propId === "period") next.period = null;
  else if (propId === "model") next.model = (next.model || []).filter((v) => v !== value);
  else if (propId === "shape") next.shape = (next.shape || []).filter((v) => v !== value);
  else if (propId === "status") next.status = (next.status || []).filter((v) => v !== value);
  else if (propId === "starred") next.starred = false;
  else if (propId === "session") next.session = null;
  return next;
}
