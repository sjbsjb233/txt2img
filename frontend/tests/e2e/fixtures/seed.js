// Provider seed fixtures — extends auth.js so specs can write
//
//   import { seedTest as test, expect } from "../fixtures/seed.js";
//
// Each provider fixture creates a real DB row via admin API at setup,
// removes it at teardown.

import { test as authTest } from "./auth.js";

const stamp = () => `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;

export const seedTest = authTest.extend({

  // Free-tier reachable BLTCY clone. Restricts gpt-image-2 to
  // n_max=4 / size=[1024x1024] / quality=[auto] — the canonical
  // "free user sees mostly greyed chips" scenario.
  bltcyRestricted: async ({ apiClient }, use) => {
    const id = `bltcy_${stamp()}`;
    const p = await apiClient.createProvider({
      provider_id: id,
      label: id,
      adapter_type: "openai_v1",
      base_url: "https://bltcy.test/v1",
      api_key: "sk-test-bltcy",
      cost_per_image_cny: 0.10,
      initial_balance_cny: 100,
      max_concurrency: 4,
      rpm_limit: 60,
      tier_access: ["free", "premium"],
      supported_models: [
        {
          model_id: "gpt-image-2",
          enabled: true,
          capabilities: {
            n_max: 4,
            size: ["1024x1024"],
            quality: ["auto"],
            output_format: ["png"],
          },
        },
      ],
    });
    await use(p);
    await apiClient.deleteProvider(id).catch(() => {});
  },

  // Premium-only Aliyun clone. Full caps, drives the "premium user
  // sees everything lit up" scenario AND lets us swap caps mid-test.
  aliyunFull: async ({ apiClient }, use) => {
    const id = `aliyun_${stamp()}`;
    const p = await apiClient.createProvider({
      provider_id: id,
      label: id,
      adapter_type: "openai_v1",
      base_url: "https://aliyun.test/v1",
      api_key: "sk-test-aliyun",
      cost_per_image_cny: 0.20,
      initial_balance_cny: 500,
      max_concurrency: 8,
      rpm_limit: 120,
      tier_access: ["premium"],
      supported_models: [
        {
          model_id: "gpt-image-2",
          enabled: true,
          capabilities: {
            n_max: 10,
            size: ["1024x1024", "1024x1536", "1536x1024", "auto"],
            quality: ["low", "medium", "high", "auto"],
            output_format: ["png", "jpeg", "webp"],
            background: ["auto", "opaque"],
            moderation: ["auto", "low"],
          },
        },
      ],
    });
    await use(p);
    await apiClient.deleteProvider(id).catch(() => {});
  },

  // Gemini Flash 3.1 fully unlocked, both tiers.
  geminiFlashFull: async ({ apiClient }, use) => {
    const id = `gemini_${stamp()}`;
    const p = await apiClient.createProvider({
      provider_id: id,
      label: id,
      adapter_type: "gemini_v1beta",
      base_url: "https://gemini.test/v1beta",
      api_key: "sk-test-gemini",
      cost_per_image_cny: 0.15,
      initial_balance_cny: 300,
      max_concurrency: 8,
      rpm_limit: 60,
      tier_access: ["free", "premium"],
      supported_models: [
        {
          model_id: "gemini-3.1-flash-image-preview",
          enabled: true,
          capabilities: {
            n_max: 1,
            aspect_ratio: ["1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "3:2",
                            "21:9", "5:4", "4:5", "1:4", "4:1", "1:8", "8:1"],
            image_size: ["512", "1K", "2K", "4K"],
            thinking_level: ["minimal", "high"],
            include_thoughts: true,
            image_search: true,
            google_search: true,
          },
        },
      ],
    });
    await use(p);
    await apiClient.deleteProvider(id).catch(() => {});
  },
});

export { expect } from "@playwright/test";
