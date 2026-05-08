// Trackpad gesture classifier for the mask editor.
//
// The browser surfaces touchpad pinches and two-finger pans as `wheel`
// events with subtle differences in modifier keys:
//   - macOS pinch  → wheel + ctrlKey === true
//   - two-finger pan → wheel + no modifiers
//   - the mask editor adds Alt / Shift conventions on top of the pan
//     case so the user can adjust brush parameters without leaving the
//     canvas.
//
// `classifyWheel` is a pure function so it can be unit-tested without a
// DOM. The CanvasStage owns the side effects (zoom anchoring, inertia,
// HUD updates).

export function classifyWheel(e) {
  if (e.ctrlKey) {
    // macOS pinch arrives here, as does an actual Control + wheel.
    // Both should mean "zoom around the pointer" inside the canvas.
    const factor = Math.exp(-e.deltaY * 0.012);
    return { kind: "zoom", dx: 0, dy: 0, factor };
  }
  if (e.altKey && e.shiftKey) {
    return { kind: "opacity", dx: 0, dy: -e.deltaY * 0.4, factor: 1 };
  }
  if (e.altKey) {
    return { kind: "brushSize", dx: 0, dy: -e.deltaY * 0.4, factor: 1 };
  }
  if (e.shiftKey) {
    return { kind: "hardness", dx: 0, dy: -e.deltaY * 0.4, factor: 1 };
  }
  return {
    kind: "pan",
    dx: -e.deltaX,
    dy: -e.deltaY,
    factor: 1,
  };
}
