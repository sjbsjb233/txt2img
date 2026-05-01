import { Outlet, useLocation } from "react-router-dom";
import AnnouncementsBanner from "./AnnouncementsBanner.jsx";
import ImpersonateBanner from "./ImpersonateBanner.jsx";
import Sidebar from "./Sidebar.jsx";

export default function Layout() {
  const location = useLocation();
  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: "var(--paper)",
        fontFamily: "var(--font-sans)",
        color: "var(--ink)",
        overflow: "hidden",
      }}
    >
      <ImpersonateBanner />
      {/* Top-of-page announcement banners stack just under the impersonate
          banner so they're visible everywhere inside the authenticated
          shell. The high-priority modal variant (priority >= 5) renders
          itself as a fixed overlay on top of the route content. */}
      <AnnouncementsBanner />
      <div
        style={{
          flex: 1,
          display: "flex",
          minHeight: 0,
        }}
      >
        <Sidebar />
        <main
          style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}
        >
          <div key={location.pathname} className="route-stage">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
