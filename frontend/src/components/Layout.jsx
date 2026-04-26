import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";

export default function Layout() {
  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        display: "flex",
        background: "var(--paper)",
        fontFamily: "var(--font-sans)",
        color: "var(--ink)",
        overflow: "hidden",
      }}
    >
      <Sidebar />
      <main style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <Outlet />
      </main>
    </div>
  );
}
