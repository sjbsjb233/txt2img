import { describe, it, expect } from "vitest";
import { reconcileParams, applyDefaults } from "./reconcileParams.js";
import { paramKey } from "./paramKey.js";

describe("paramKey", () => {
  it("returns value_key when set", () => {
    expect(paramKey({ k: "n_max", value_key: "n" })).toBe("n");
  });
  it("falls back to k when value_key undefined", () => {
    expect(paramKey({ k: "size" })).toBe("size");
  });
  it("falls back to k when value_key is empty string", () => {
    expect(paramKey({ k: "size", value_key: "" })).toBe("size");
  });
});

describe("reconcileParams", () => {

  it("drops params for fields not in ui_schema", () => {
    /** Stale params from a previous model (e.g. aspect_ratio carried
     * over from gemini → gpt-image-2) must NOT survive — they would
     * leak into the request body and trigger a 422. */
    const out = reconcileParams(
      { aspect_ratio: "1:1", custom_unknown: "foo" },
      {},
      []
    );
    expect(out).toEqual({});
  });

  it("preserves submission-glue keys even when not in schema", () => {
    const out = reconcileParams(
      {
        prompt: "x", model: "gpt-image-2", session_id: "s",
        client_request_id: "r", captcha_token: "c",
      },
      {},
      []
    );
    expect(out.prompt).toBe("x");
    expect(out.model).toBe("gpt-image-2");
    expect(out.session_id).toBe("s");
    expect(out.client_request_id).toBe("r");
    expect(out.captcha_token).toBe("c");
  });

  it("clears chip value when current value not in capabilities", () => {
    const out = reconcileParams(
      { size: "4096x4096" },
      { size: ["1024x1024"] },
      [{ k: "size", control: "chip-row", options: ["1024x1024", "4096x4096"] }]
    );
    expect(out.size).toBeUndefined();
  });

  it("keeps chip value when current value IS in capabilities", () => {
    const out = reconcileParams(
      { size: "1024x1024" },
      { size: ["1024x1024"] },
      [{ k: "size", control: "chip-row", options: ["1024x1024"] }]
    );
    expect(out.size).toBe("1024x1024");
  });

  it("truncates number value when exceeds capability (uses value_key)", () => {
    const out = reconcileParams(
      { n: 10 },
      { n_max: 4 },
      [{ k: "n_max", value_key: "n", control: "number", min: 1, max: 10 }]
    );
    expect(out.n).toBe(4);
  });

  it("does NOT write to params.n_max even though k=n_max", () => {
    /** Bug regression: schema has k=n_max + value_key=n. The capability
     * is named n_max in caps, but the request param key is n. The
     * cell's onChange must end up at params.n, not params.n_max. */
    const out = reconcileParams(
      { n: 4, n_max: 999 },          // 999 is junk left from somewhere
      { n_max: 8 },
      [{ k: "n_max", value_key: "n", control: "number", min: 1, max: 10 }]
    );
    expect(out.n).toBe(4);
    expect(out.n_max).toBeUndefined();
  });

  it("forces toggle to false when capability not true", () => {
    const out = reconcileParams(
      { image_search: true },
      { image_search: false },
      [{ k: "image_search", control: "toggle" }]
    );
    expect(out.image_search).toBe(false);
  });

  it("keeps toggle false when user explicitly set false", () => {
    const out = reconcileParams(
      { image_search: false },
      { image_search: true },
      [{ k: "image_search", control: "toggle" }]
    );
    expect(out.image_search).toBe(false);
  });

  it("does not mutate input params object", () => {
    const params = { size: "4096x4096" };
    const original = { ...params };
    reconcileParams(params, { size: ["1024x1024"] }, [
      { k: "size", control: "chip-row", options: ["1024x1024", "4096x4096"] },
    ]);
    expect(params).toEqual(original);
  });

  it("handles capabilities=null without crashing", () => {
    const out = reconcileParams({ prompt: "x" }, null, []);
    expect(out).toEqual({ prompt: "x" });
  });

  it("handles ui_schema=null without crashing", () => {
    const out = reconcileParams({ prompt: "x" }, {}, null);
    expect(out).toEqual({ prompt: "x" });
  });
});

describe("applyDefaults", () => {

  it("fills missing keys from defaults", () => {
    const out = applyDefaults(
      { quality: "auto", n: 1 },
      { prompt: "x" },
      { quality: ["auto"], n_max: 4 },
      [
        { k: "quality", control: "chip-row", options: ["auto"] },
        { k: "n_max", value_key: "n", control: "number", min: 1, max: 10 },
      ]
    );
    expect(out.quality).toBe("auto");
    expect(out.n).toBe(1);
    expect(out.prompt).toBe("x");
  });

  it("does not overwrite user-set values", () => {
    const out = applyDefaults(
      { quality: "auto" },
      { quality: "high", prompt: "x" },
      { quality: ["high", "auto"] },
      [{ k: "quality", control: "chip-row", options: ["high", "auto"] }]
    );
    expect(out.quality).toBe("high");
  });

  it("scrubs stale params after applying defaults", () => {
    const out = applyDefaults(
      { quality: "auto" },
      { aspect_ratio: "1:1", quality: "auto" },  // aspect_ratio is stale
      { quality: ["auto"] },
      [{ k: "quality", control: "chip-row", options: ["auto"] }]
    );
    expect(out.aspect_ratio).toBeUndefined();
    expect(out.quality).toBe("auto");
  });
});
