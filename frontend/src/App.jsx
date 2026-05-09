import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import LoginPage from "./pages/LoginPage.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import CreatePage from "./pages/CreatePage.jsx";
import BatchPage from "./pages/BatchPage.jsx";
import ArchivePage from "./pages/ArchivePage.jsx";
import PickerPage from "./pages/PickerPage.jsx";
import AdminPage from "./pages/AdminPage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import MaskEditPage from "./pages/MaskEditPage.jsx";
import Layout from "./components/Layout.jsx";
import RequireAuth from "./components/RequireAuth.jsx";
import RequireAdmin from "./components/RequireAdmin.jsx";
import ConnectionLost from "./components/ConnectionLost.jsx";
import { useAuth } from "./store/auth.js";
import {
  connect as connectSSE,
  disconnect as disconnectSSE,
  useConnectionState,
} from "./store/sse.js";
import * as announcementsStore from "./store/announcements.js";
import * as archiveStore from "./store/archive.js";
import * as batchStore from "./store/batch.js";
import * as preferencesStore from "./store/preferences.js";
import * as sseStoreModule from "./store/sse.js";
import * as maskDraftDB from "./storage/maskDraftDB.js";

export default function App() {
  const { isAuthenticated, user } = useAuth();
  const conn = useConnectionState();

  // Open the SSE connection whenever the user has a token, close it
  // when they don't. The SSE client itself lives in store/sse.js — this
  // effect only owns its lifecycle relative to auth state. We re-run on
  // every isAuthenticated change so a logout-then-login flow lands on a
  // brand-new stream (no leaked subscribers, no stale Last-Event-ID
  // pointing at the previous user's buffer).
  useEffect(() => {
    if (isAuthenticated) {
      connectSSE();
      return () => disconnectSSE();
    }
    disconnectSSE();
    return undefined;
  }, [isAuthenticated]);

  // Mount the archive store at the App level so optimistic inserts from
  // Create work regardless of whether ArchivePage is currently rendered.
  // The store hydrates from IndexedDB, runs a delta sync, and starts
  // listening to SSE events — so by the time the user navigates to
  // /archive (or hits Generate from /create) everything is already warm.
  useEffect(() => {
    if (isAuthenticated && user?.id) {
      void archiveStore.mount(user.id);
      // Cheap one-shot prune of stale mask drafts (TTL + LRU). Doesn't
      // need to block anything; failures are silently ignored.
      maskDraftDB.prune(user.id).catch(() => {});
    } else {
      archiveStore.unmount();
    }
  }, [isAuthenticated, user?.id]);

  // Same lifecycle for the announcements store — mounting hydrates the
  // active list from /api/announcements/active and subscribes to SSE.
  // Mounting at the App level means the banner stack lives across
  // route changes and a freshly-published announcement can appear on
  // any page within milliseconds of the admin clicking Publish.
  useEffect(() => {
    if (isAuthenticated && user?.id) {
      void announcementsStore.mount(user.id);
    } else {
      announcementsStore.unmount();
    }
  }, [isAuthenticated, user?.id]);

  // The batch store mounts here too so the /batch page renders fresh
  // running cards on first visit (frontend doc v0.3 §5.4).
  useEffect(() => {
    if (isAuthenticated && user?.id) {
      void batchStore.mount(user.id);
    } else {
      batchStore.unmount();
    }
  }, [isAuthenticated, user?.id]);

  // Load synced preferences once we have a token. Theme / density /
  // sidebar default depend on this — preferencesStore applies them to
  // <html> as data-attributes so the rest of the app picks them up
  // through CSS without needing a React subscription.
  useEffect(() => {
    if (isAuthenticated && user?.id) {
      void preferencesStore.load();
    }
  }, [isAuthenticated, user?.id]);

  // Browser-notification subscriber: when a job ends in SUCCEEDED or
  // FAILED while another tab is foregrounded, fire a Notification
  // (the user opted in from Settings → Notifications). This is the
  // *only* job-level browser surface, so it lives at the App layer
  // alongside the other long-lived subscriptions.
  useEffect(() => {
    if (!isAuthenticated) return undefined;
    const off = sseStoreModule.subscribe("job_state", (payload) => {
      const prefs = preferencesStore.getMerged();
      if (!prefs?.notifications?.browser_on_complete) return;
      if (typeof Notification === "undefined") return;
      if (Notification.permission !== "granted") return;
      if (typeof document !== "undefined" && !document.hidden) return;
      const status = payload?.to;
      if (status !== "SUCCEEDED" && status !== "FAILED") return;
      try {
        new Notification(
          status === "SUCCEEDED" ? "Generation complete" : "Generation failed",
          {
            body: payload?.prompt
              ? String(payload.prompt).slice(0, 80)
              : `Job ${payload?.hash_id || ""}`,
            tag: `job-${payload?.hash_id || payload?.job_id || Math.random()}`,
          }
        );
      } catch {
        /* ignore — some browsers throw if the tab is closing */
      }
    });
    return off;
  }, [isAuthenticated]);

  // Show the full-screen "connection lost" overlay only after the SSE
  // client has tried at least 5 times in a row to reconnect. It hides
  // automatically when status flips back to ``open``.
  const showLost = conn.status === "lost";

  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        {/* `/` lands on the dashboard, but the canonical path is /dashboard
            so the URL bar matches the sidebar entry and external links work. */}
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/create" element={<CreatePage />} />
          <Route path="/batch" element={<BatchPage />} />
          <Route path="/picker" element={<PickerPage />} />
          <Route path="/archive" element={<ArchivePage />} />
          <Route
            path="/edit/:hashId/:order"
            element={<MaskEditPage />}
          />
          <Route path="/settings" element={<SettingsPage />} />
          <Route
            path="/admin"
            element={
              <RequireAdmin>
                <AdminPage />
              </RequireAdmin>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
      {showLost && <ConnectionLost attempts={conn.attempts || 5} />}
    </>
  );
}
