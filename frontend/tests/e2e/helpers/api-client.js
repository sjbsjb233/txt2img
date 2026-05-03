// Thin REST client used by Playwright fixtures to seed users and
// providers via the admin API. Talks directly to the backend so the
// frontend doesn't need to drive admin login flows in setup.

import { request as pwRequest } from "@playwright/test";

const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME || "admin";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || "test-admin-password";

export class ApiClient {
  constructor(baseURL) {
    this.baseURL = baseURL || "http://127.0.0.1:8000";
    this._adminToken = null;
    this._ctx = null;
  }

  async _context() {
    if (!this._ctx) this._ctx = await pwRequest.newContext({ baseURL: this.baseURL });
    return this._ctx;
  }

  async dispose() {
    if (this._ctx) {
      await this._ctx.dispose();
      this._ctx = null;
    }
  }

  async login(username, password) {
    const ctx = await this._context();
    const r = await ctx.post("/api/auth/login", {
      data: { username, password },
    });
    if (!r.ok()) {
      throw new Error(
        `login(${username}) failed: ${r.status()} ${await r.text()}`
      );
    }
    return (await r.json()).access_token;
  }

  async _adminAuth() {
    if (!this._adminToken) {
      this._adminToken = await this.login(ADMIN_USERNAME, ADMIN_PASSWORD);
    }
    return { Authorization: `Bearer ${this._adminToken}` };
  }

  async createUser({ username, password, tier = "free", role = "user" }) {
    const ctx = await this._context();
    const r = await ctx.post("/api/admin/users", {
      headers: await this._adminAuth(),
      data: { username, password, tier, role },
    });
    if (!r.ok()) {
      throw new Error(
        `createUser(${username}) failed: ${r.status()} ${await r.text()}`
      );
    }
    return { ...(await r.json()), password };
  }

  async deleteUser(userId) {
    const ctx = await this._context();
    await ctx.delete(`/api/admin/users/${userId}`, {
      headers: await this._adminAuth(),
    });
  }

  async createProvider(body) {
    const ctx = await this._context();
    const r = await ctx.post("/api/admin/providers", {
      headers: await this._adminAuth(),
      data: body,
    });
    if (!r.ok()) {
      throw new Error(
        `createProvider(${body.provider_id}) failed: ${r.status()} ${await r.text()}`
      );
    }
    return await r.json();
  }

  async patchProvider(providerId, patch) {
    const ctx = await this._context();
    const r = await ctx.patch(`/api/admin/providers/${providerId}`, {
      headers: await this._adminAuth(),
      data: patch,
    });
    if (!r.ok()) {
      throw new Error(
        `patchProvider(${providerId}) failed: ${r.status()} ${await r.text()}`
      );
    }
    return await r.json();
  }

  async deleteProvider(providerId) {
    const ctx = await this._context();
    await ctx.delete(`/api/admin/providers/${providerId}`, {
      headers: await this._adminAuth(),
    });
  }
}
