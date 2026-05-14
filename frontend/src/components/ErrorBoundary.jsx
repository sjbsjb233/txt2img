import React from "react";
import { log } from "../utils/logger.js";

// Top-level React error boundary. Mounted just inside <BrowserRouter> by
// main.jsx so any render-time / lifecycle exception inside a route is
// surfaced both to the user (with a tiny fallback) and to the backend
// via /api/client-logs.
//
// This intentionally does NOT try to be a fancy "something went wrong"
// page — we render a minimal centred message and rely on the in-app
// log reporting to bring back the stack trace. Once the user navigates
// away (route change) we recover by clearing the captured error.

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: null };
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true,
      message:
        (error && (error.message || String(error))) ||
        "Unexpected render error",
    };
  }

  componentDidCatch(error, info) {
    try {
      log.error("react boundary", {
        msg: error?.message,
        stack: error?.stack,
        component_stack: info?.componentStack,
      });
    } catch (_e) {
      /* ignore — telemetry must not throw */
    }
  }

  componentDidUpdate(prevProps) {
    // Recover on route changes — Router passes a different `location`
    // through context, but the cheap signal is just the URL key here.
    if (this.state.hasError && this.props.locationKey !== prevProps.locationKey) {
      this.setState({ hasError: false, message: null });
    }
  }

  handleReload = () => {
    try {
      window.location.reload();
    } catch (_e) {
      /* ignore */
    }
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          style={{
            minHeight: "60vh",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "24px",
            textAlign: "center",
            color: "var(--color-text, #222)",
          }}
        >
          <h2 style={{ marginBottom: 12 }}>页面渲染出错</h2>
          <p style={{ marginBottom: 16, opacity: 0.75 }}>
            错误信息已自动上报，刷新页面即可恢复。
          </p>
          <pre
            style={{
              maxWidth: "640px",
              padding: "12px 16px",
              fontSize: 12,
              background: "rgba(0,0,0,0.06)",
              borderRadius: 6,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              marginBottom: 16,
            }}
          >
            {this.state.message || "Unknown error"}
          </pre>
          <button
            type="button"
            onClick={this.handleReload}
            style={{
              padding: "8px 18px",
              borderRadius: 6,
              border: "none",
              background: "var(--color-primary, #2563eb)",
              color: "#fff",
              cursor: "pointer",
            }}
          >
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
