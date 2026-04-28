import { Routes, Route, Navigate } from "react-router-dom";
import LoginPage from "./pages/LoginPage.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import CreatePage from "./pages/CreatePage.jsx";
import ArchivePage from "./pages/ArchivePage.jsx";
import Layout from "./components/Layout.jsx";
import RequireAuth from "./components/RequireAuth.jsx";

export default function App() {
  return (
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
        <Route path="/archive" element={<ArchivePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
