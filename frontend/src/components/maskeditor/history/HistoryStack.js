// 50-step undo/redo for the mask layer.
//
// Stores diff bitmaps as ImageData snapshots — a 1024² mask at one
// byte per pixel weighs ~1MB, so a full 50-step history is ~50MB
// worst-case. That's fine for the editor's lifetime; we evict from
// the bottom of the stack once we hit the cap.

const MAX_STEPS = 50;

export function createHistoryStack(initialSnapshot, initialMeta) {
  const steps = [{ snapshot: initialSnapshot, meta: initialMeta }];
  let cursor = 0;

  const api = {
    push(snapshot, meta) {
      // Discard "future" entries when a new step is added past undo.
      if (cursor < steps.length - 1) {
        steps.splice(cursor + 1);
      }
      steps.push({ snapshot, meta });
      if (steps.length > MAX_STEPS) {
        steps.shift();
      } else {
        cursor++;
      }
      cursor = steps.length - 1;
    },
    undo() {
      if (cursor === 0) return null;
      cursor--;
      return steps[cursor];
    },
    redo() {
      if (cursor >= steps.length - 1) return null;
      cursor++;
      return steps[cursor];
    },
    jumpTo(idx) {
      if (idx < 0 || idx >= steps.length) return null;
      cursor = idx;
      return steps[cursor];
    },
    canUndo() { return cursor > 0; },
    canRedo() { return cursor < steps.length - 1; },
    getList() { return steps.map((s, i) => ({ ...s, index: i, active: i === cursor })); },
    getCursor() { return cursor; },
    getCount() { return steps.length; },
  };
  return api;
}
