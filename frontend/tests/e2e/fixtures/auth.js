// Playwright fixtures for authenticated user / admin sessions.
//
// Each fixture creates a fresh user via the admin API, hands the
// browser a token via localStorage initScript, then disposes of the
// user at teardown. The provider seeding sits in seed.js to keep
// concerns separate.

import { test as base } from "@playwright/test";
import { ApiClient } from "../helpers/api-client.js";

const API_BASE = process.env.E2E_API_BASE || "http://127.0.0.1:8000";

async function _injectAuth(page, { token, user }) {
  await page.addInitScript(
    ({ token, user, apiBase }) => {
      localStorage.setItem("token", token);
      localStorage.setItem("user", JSON.stringify(user));
      localStorage.setItem("api_base", apiBase);
    },
    { token, user, apiBase: API_BASE }
  );
}

export const test = base.extend({
  apiClient: async ({}, use) => {
    const client = new ApiClient(API_BASE);
    await use(client);
    await client.dispose();
  },

  freeUser: async ({ apiClient }, use) => {
    const u = await apiClient.createUser({
      username: `alice_${Date.now()}_${Math.floor(Math.random() * 1e6)}`,
      password: "alice-pwd-12",
      tier: "free",
    });
    await use(u);
    await apiClient.deleteUser(u.id).catch(() => {});
  },

  premiumUser: async ({ apiClient }, use) => {
    const u = await apiClient.createUser({
      username: `bob_${Date.now()}_${Math.floor(Math.random() * 1e6)}`,
      password: "bob-pwd-1234",
      tier: "premium",
    });
    await use(u);
    await apiClient.deleteUser(u.id).catch(() => {});
  },

  freeUserPage: async ({ page, apiClient, freeUser }, use) => {
    const token = await apiClient.login(freeUser.username, freeUser.password);
    await _injectAuth(page, { token, user: freeUser });
    await use(page);
  },

  premiumUserPage: async ({ page, apiClient, premiumUser }, use) => {
    const token = await apiClient.login(premiumUser.username, premiumUser.password);
    await _injectAuth(page, { token, user: premiumUser });
    await use(page);
  },
});

export { expect } from "@playwright/test";
