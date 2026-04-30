import { useEffect, useState } from "react";
import * as adminProviders from "../../api/admin/providers.js";
import { Hair } from "./atoms.jsx";

// Read-only registry view. Adapters are .py files under
// `backend/app/adapters/` — to add or remove one, drop the file and
// restart the process. There's no admin write API.

export default function AdaptersTab() {
  const [adapters, setAdapters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await adminProviders.listAdapters();
        if (alive) setAdapters(list || []);
      } catch (err) {
        if (alive) setError(err.message || "Failed to load adapters.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div>
        <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
          GET /api/admin/adapters
        </div>
        <div
          className="display"
          style={{ fontSize: 32, fontWeight: 800, letterSpacing: "-0.025em", marginTop: 6 }}
        >
          Registered{" "}
          <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
            adapters.
          </span>
        </div>
        <div
          style={{
            fontSize: 13,
            color: "var(--ink-3)",
            marginTop: 6,
            fontStyle: "italic",
            fontFamily: "var(--font-display)",
          }}
        >
          Read-only. Drop a new adapter into <code>backend/app/adapters/</code>{" "}
          and restart to register.
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: 10,
            border: "1px solid var(--bad)",
            color: "var(--bad)",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      {loading && (
        <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
          loading adapters…
        </div>
      )}

      <Hair thick />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {adapters.map((a) => (
          <div
            key={a.adapter_type}
            style={{
              border: "1px solid var(--ink)",
              background: "#fffdf7",
              padding: 16,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <div className="mono" style={{ fontSize: 14, fontWeight: 700 }}>
                {a.adapter_type}
              </div>
              <span className="chip">
                {(a.in_use_by_providers || []).length} provider(s)
              </span>
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, marginTop: 6 }}>
              {a.display_name}
            </div>
            <div
              style={{
                fontSize: 12,
                color: "var(--ink-3)",
                marginTop: 4,
                fontFamily: "var(--font-display)",
                fontStyle: "italic",
              }}
            >
              {a.description}
            </div>
            <div style={{ marginTop: 12 }}>
              <div
                className="mono caps"
                style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 4 }}
              >
                SUPPORTED MODELS
              </div>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {(a.supported_models || []).map((m) => (
                  <span key={m} className="chip" style={{ fontSize: 10 }}>
                    {m}
                  </span>
                ))}
              </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <div
                className="mono caps"
                style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 4 }}
              >
                IN USE BY
              </div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-2)" }}>
                {(a.in_use_by_providers || []).length === 0
                  ? "— none —"
                  : (a.in_use_by_providers || []).join(", ")}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
