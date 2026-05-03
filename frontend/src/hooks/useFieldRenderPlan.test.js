import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useFieldRenderPlan, buildPlan } from "./useFieldRenderPlan.js";

const chip = (over = {}) => ({
  k: "size",
  control: "chip-row",
  label: "Size",
  options: ["a", "b", "c"],
  group: "primary",
  order: 10,
  ...over,
});

const number = (over = {}) => ({
  k: "n_max",
  value_key: "n",
  control: "number",
  label: "Output count",
  group: "primary",
  order: 10,
  min: 1,
  max: 10,
  presets: [1, 2, 4, 8],
  ...over,
});

const toggle = (over = {}) => ({
  k: "image_search",
  control: "toggle",
  label: "Image search",
  group: "advanced",
  order: 30,
  ...over,
});

describe("useFieldRenderPlan", () => {

  describe("input edge cases", () => {

    it("returns empty plan on undefined ui_schema", () => {
      const { result } = renderHook(() => useFieldRenderPlan(undefined, {}));
      expect(result.current.primary).toEqual([]);
      expect(result.current.advanced).toEqual([]);
    });

    it("returns empty plan on null ui_schema", () => {
      const { result } = renderHook(() => useFieldRenderPlan(null, {}));
      expect(result.current.primary).toEqual([]);
    });

    it("returns empty plan on empty array", () => {
      const { result } = renderHook(() => useFieldRenderPlan([], {}));
      expect(result.current.primary).toEqual([]);
    });

    it("handles undefined capabilities gracefully", () => {
      const { result } = renderHook(() => useFieldRenderPlan([chip()], undefined));
      expect(result.current.primary[0].fieldDisabled).toBe(true);
    });

    it("handles null capabilities gracefully", () => {
      const { result } = renderHook(() => useFieldRenderPlan([chip()], null));
      expect(result.current.primary[0].fieldDisabled).toBe(true);
    });
  });

  describe("list-type fields", () => {

    it("marks all options allowed when capabilities is full set", () => {
      const plan = buildPlan([chip()], { size: ["a", "b", "c"] });
      const p = plan.primary[0];
      expect(p.fieldDisabled).toBe(false);
      expect(p.allowedOptions.has("a")).toBe(true);
      expect(p.allowedOptions.has("c")).toBe(true);
    });

    it("marks subset options as allowed, others as disabled", () => {
      const plan = buildPlan([chip()], { size: ["a"] });
      const p = plan.primary[0];
      expect(p.fieldDisabled).toBe(false);
      expect(p.allowedOptions.has("a")).toBe(true);
      expect(p.allowedOptions.has("b")).toBe(false);
      expect(p.allowedOptions.has("c")).toBe(false);
    });

    it("disables entire field when capabilities is empty array", () => {
      const plan = buildPlan([chip()], { size: [] });
      expect(plan.primary[0].fieldDisabled).toBe(true);
    });

    it("disables entire field when capabilities key missing", () => {
      const plan = buildPlan([chip()], {});
      expect(plan.primary[0].fieldDisabled).toBe(true);
    });

    it("disables entire field when capabilities key is null", () => {
      const plan = buildPlan([chip()], { size: null });
      expect(plan.primary[0].fieldDisabled).toBe(true);
    });

    it("treats chip-grid same as chip-row for option allowance", () => {
      const plan = buildPlan([chip({ control: "chip-grid" })], { size: ["b"] });
      const p = plan.primary[0];
      expect(p.allowedOptions.has("a")).toBe(false);
      expect(p.allowedOptions.has("b")).toBe(true);
    });

    it("treats select same as chip-row for option allowance", () => {
      const plan = buildPlan([chip({ control: "select" })], { size: ["c"] });
      expect(plan.primary[0].allowedOptions.has("c")).toBe(true);
    });
  });

  describe("number-type fields", () => {

    it("field enabled when capabilities > min", () => {
      const plan = buildPlan([number()], { n_max: 4 });
      expect(plan.primary[0].fieldDisabled).toBe(false);
    });

    it("field renders interactive when locked to single value (n=1)", () => {
      // Gemini case — max=1, presets=[1]
      const plan = buildPlan(
        [number({ max: 1, presets: [1] })],
        { n_max: 1 }
      );
      // We render it (layout stability) but it has only one preset
      expect(plan.primary[0].fieldDisabled).toBe(false);
    });

    it("disables field when capability < schema.min", () => {
      const plan = buildPlan(
        [number({ min: 2, max: 10 })],
        { n_max: 1 }
      );
      expect(plan.primary[0].fieldDisabled).toBe(true);
    });
  });

  describe("toggle-type fields", () => {

    it("toggles enabled only when capability is true", () => {
      const cases = [
        [true, false],   // cap true → enabled
        [false, true],   // cap false → disabled
        [null, true],    // cap null → disabled
        [undefined, true],
      ];
      for (const [cap, expectedDisabled] of cases) {
        const plan = buildPlan([toggle()], { image_search: cap });
        expect(plan.advanced[0].fieldDisabled).toBe(expectedDisabled);
      }
    });

    it("emits different reason for false vs null", () => {
      const r1 = buildPlan([toggle()], { image_search: false });
      const r2 = buildPlan([toggle()], { image_search: null });
      expect(r1.advanced[0].disabledReason).not.toBe(r2.advanced[0].disabledReason);
    });
  });

  describe("grouping & ordering", () => {

    it("separates primary vs advanced", () => {
      const plan = buildPlan(
        [chip(), toggle()],
        { size: ["a"], image_search: true }
      );
      expect(plan.primary).toHaveLength(1);
      expect(plan.advanced).toHaveLength(1);
    });

    it("sorts within group by order ascending", () => {
      const a = chip({ k: "a", order: 30 });
      const b = chip({ k: "b", order: 10 });
      const c = chip({ k: "c", order: 20 });
      const plan = buildPlan([a, b, c], { a: ["a"], b: ["a"], c: ["a"] });
      expect(plan.primary.map((p) => p.field.k)).toEqual(["b", "c", "a"]);
    });

    it("treats missing group as primary", () => {
      const f = { k: "x", control: "chip-row", label: "X", options: ["a"], order: 10 };
      const plan = buildPlan([f], { x: ["a"] });
      expect(plan.primary).toHaveLength(1);
      expect(plan.advanced).toHaveLength(0);
    });

    it("treats missing order as 100", () => {
      const a = chip({ k: "a" });             // order=10
      const b = chip({ k: "b", order: undefined });  // → 100
      delete b.order;
      const plan = buildPlan([a, b], { a: ["a"], b: ["a"] });
      expect(plan.primary.map((p) => p.field.k)).toEqual(["a", "b"]);
    });
  });

  describe("memoization", () => {

    it("returns the same reference when inputs unchanged", () => {
      const schema = [chip()];
      const caps = { size: ["a"] };
      const { result, rerender } = renderHook(
        ({ s, c }) => useFieldRenderPlan(s, c),
        { initialProps: { s: schema, c: caps } }
      );
      const first = result.current;
      rerender({ s: schema, c: caps });
      expect(result.current).toBe(first);
    });

    it("returns a new reference when capabilities change", () => {
      const schema = [chip()];
      const { result, rerender } = renderHook(
        ({ s, c }) => useFieldRenderPlan(s, c),
        { initialProps: { s: schema, c: { size: ["a"] } } }
      );
      const first = result.current;
      rerender({ s: schema, c: { size: ["a", "b"] } });
      expect(result.current).not.toBe(first);
    });
  });
});
