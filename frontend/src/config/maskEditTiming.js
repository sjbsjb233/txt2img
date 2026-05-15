// Tracks observed render durations per model so the GeneratingCard's
// "estimated remaining" countdown is grounded in reality rather than a
// single hard-coded constant. Stored in localStorage so a tab close /
// refresh keeps the data — IDB would be overkill for a flat
// model→array-of-numbers map under 1 KB.

const KEY = "mask_edit_timings_v1";
const KEEP = 8; // newest N samples per model
const FALLBACK_AVG = 24; // cold-start guess in seconds

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function write(map) {
  try {
    localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    // ignored — quota exceeded just means we don't get smarter this session
  }
}

function avg(arr) {
  if (!arr || !arr.length) return 0;
  let sum = 0;
  for (const v of arr) sum += v;
  return sum / arr.length;
}

export function recordMaskEditDuration(model, seconds) {
  if (!model || !(seconds > 0)) return;
  const all = read();
  const arr = (all[model] || []).concat(seconds).slice(-KEEP);
  all[model] = arr;
  write(all);
}

// Returns an estimated render duration in seconds, derived from the
// most recent samples for the model. Falls back to the pooled average
// across all models when the per-model history is too short, then to
// FALLBACK_AVG when there's nothing at all.
export function getEstimatedDuration(model) {
  const all = read();
  const own = all[model] || [];
  if (own.length >= 2) return avg(own);
  const pooled = Object.values(all).flat();
  if (pooled.length >= 2) return avg(pooled);
  return FALLBACK_AVG;
}
