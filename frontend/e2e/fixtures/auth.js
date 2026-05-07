// Login + storageState helpers for the e2e_picker_user account.

import { request } from "@playwright/test";

export const E2E_USER = {
  username: "e2e_picker_user",
  password: "e2e-test-pass-1234567890",
};

export const API_BASE = process.env.E2E_API_BASE || "http://127.0.0.1:18000";
export const APP_BASE = process.env.E2E_BASE_URL || "http://localhost:5173";

export async function loginGetToken() {
  const ctx = await request.newContext();
  try {
    const resp = await ctx.post(`${API_BASE}/api/auth/login`, {
      data: E2E_USER,
    });
    if (!resp.ok()) {
      throw new Error(`login failed: HTTP ${resp.status()}`);
    }
    const body = await resp.json();
    return { token: body.access_token, user: body.user };
  } finally {
    await ctx.dispose();
  }
}

/**
 * Inject a token into the page's localStorage so the SPA boots logged in.
 * Mirrors how the real app's LoginPage stores the token + user object.
 */
export async function injectAuth(page, { token, user }) {
  // Visit the page first so localStorage is scoped to the right origin.
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ({ token, user, apiBase }) => {
      localStorage.setItem("token", token);
      localStorage.setItem("user", JSON.stringify(user));
      // Optional: persist API base override so dev server uses the right port.
      if (apiBase) localStorage.setItem("api_base", apiBase);
    },
    { token, user, apiBase: API_BASE }
  );
}
