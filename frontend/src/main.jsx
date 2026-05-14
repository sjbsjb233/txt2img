import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { installGlobalHandlers, log } from "./utils/logger.js";
import "./styles/tokens.css";
import "./styles/archive.css";
import "./styles/maskeditor.css";
import "./styles/picker.css";

// Wire window.onerror / unhandledrejection before any user code runs so a
// crash during initial render is captured. The logger itself owns
// flushing on visibilitychange / pagehide.
installGlobalHandlers();

try {
  log.info("app boot", {
    href: typeof window !== "undefined" ? window.location.href : null,
  });
} catch (_e) {
  /* ignore */
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  </React.StrictMode>
);
