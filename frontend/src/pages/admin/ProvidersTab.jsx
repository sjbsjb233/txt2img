import { useCallback, useEffect, useState } from "react";
import Icon from "../../components/Icon.jsx";
import * as adminProviders from "../../api/admin/providers.js";
import { Hair, StatusDot } from "./atoms.jsx";
import ProviderEditorDialog from "./ProviderEditorDialog.jsx";
import TestSuiteMenu from "./TestSuiteMenu.jsx";
import ProviderTestSuiteDrawer from "./ProviderTestSuiteDrawer.jsx";

const ALL_TIERS = ["vip", "premium", "standard", "free"];

const STATE_TONE = {
  healthy: "ok",
  half_open: "warn",
  open: "bad",
  drained: "muted",
  disabled: "muted",
};

function ProviderCard({
  p,
  onEdit,
  onTest,
  onRunSuite,
  onTopup,
  onResetCircuit,
  onDelete,
}) {
  const drained = p.circuit_state === "drained";
  const tone = STATE_TONE[p.circuit_state] || "muted";

  // Coalesce per-(provider, model) metrics into a single "best signal"
  // line at the bottom of the card. Picking the model with the most
  // calls in the window gives a representative number; if no model
  // has traffic we show "no traffic".
  const metrics = (p.metrics || []).slice().sort((a, b) => b.calls - a.calls);
  const top = metrics[0];

  return (
    <div
      data-test={`provider-card-${p.id}`}
      style={{
        border: "1px solid var(--ink)",
        background: "#fffdf7",
        position: "relative",
        opacity: p.enabled ? 1 : 0.7,
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: drained ? "var(--paper-2)" : "var(--ink)",
          color: drained ? "var(--ink)" : "var(--paper)",
          borderBottom: "1px solid var(--ink)",
        }}
      >
        <div
          style={{
            width: 28,
            height: 28,
            background: "var(--banana)",
            color: "var(--ink)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid var(--ink)",
          }}
        >
          {p.id.slice(0, 2).toUpperCase()}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{p.label}</div>
          <div className="mono" style={{ fontSize: 10, opacity: 0.75 }}>
            {p.id} · {p.adapter_type}
          </div>
        </div>
        <div
          style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}
        >
          <StatusDot
            tone={tone}
            label={(p.circuit_state || "unknown").toUpperCase()}
          />
          {p.cooldown_until && (
            <span className="mono" style={{ fontSize: 10, opacity: 0.85 }}>
              cd {new Date(p.cooldown_until).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      <div
        style={{
          padding: "14px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div
          className="mono"
          style={{ fontSize: 11, color: "var(--ink-2)", wordBreak: "break-all" }}
        >
          {p.base_url} <span style={{ color: "var(--ink-3)" }}>·</span>{" "}
          <span style={{ color: "var(--ink-3)" }}>key</span>{" "}
          {p.api_key_masked}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 8,
          }}
        >
          {[
            {
              l: "BALANCE",
              v: `¥${Number(p.balance_cny).toFixed(2)}`,
              sub: `of ¥${Number(p.initial_balance_cny).toFixed(2)}`,
              danger: p.balance_cny < 0.5,
            },
            {
              l: "COST/IMG",
              v: `¥${Number(p.cost_per_image_cny).toFixed(2)}`,
            },
            {
              l: "CONC",
              v: `${p.current_concurrency ?? 0}/${p.max_concurrency}`,
            },
            { l: "RPM 60s", v: `${p.recent_calls_60s ?? 0}/${p.rpm_limit}` },
          ].map((m) => (
            <div
              key={m.l}
              style={{
                padding: "8px 10px",
                border: "1px solid var(--ink-4)",
                background: m.danger ? "#f3d6d0" : "var(--paper-2)",
              }}
            >
              <div
                className="mono caps"
                style={{
                  fontSize: 8,
                  color: "var(--ink-3)",
                  letterSpacing: "0.14em",
                }}
              >
                {m.l}
              </div>
              <div
                className="mono"
                style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: m.danger ? "var(--bad)" : "var(--ink)",
                  marginTop: 2,
                }}
              >
                {m.v}
              </div>
              {m.sub && (
                <div
                  className="mono"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  {m.sub}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          <span style={{ color: "var(--ink)" }}>5min:</span>{" "}
          {top ? (
            <>
              {top.calls} calls{" · "}
              <span
                style={{
                  color:
                    top.success_rate >= 0.99
                      ? "var(--ok)"
                      : top.success_rate < 0.95
                        ? "var(--bad)"
                        : "var(--ink-2)",
                  fontWeight: 700,
                }}
              >
                {(top.success_rate * 100).toFixed(1)}% ok
              </span>
              {top.p50_ms != null && <> · p50 {Math.round(top.p50_ms)}ms</>}
            </>
          ) : (
            <span style={{ color: "var(--ink-3)" }}>no traffic</span>
          )}
        </div>

        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 6 }}
          >
            SUPPORTED MODELS
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {(p.supported_models || []).map((m) => (
              <span
                key={m.model_id}
                className="chip"
                style={{
                  fontSize: 10,
                  opacity: m.enabled ? 1 : 0.55,
                  background: m.enabled ? undefined : "var(--paper-3)",
                }}
              >
                {m.model_id}
                {!m.enabled && " · off"}
              </span>
            ))}
            {(p.supported_models || []).length === 0 && (
              <span
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)", fontStyle: "italic" }}
              >
                no models attached
              </span>
            )}
          </div>
        </div>

        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 6 }}
          >
            TIER ACCESS
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {ALL_TIERS.map((t) => (
              <span
                key={t}
                style={{
                  padding: "2px 7px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  fontWeight: 700,
                  border: "1px solid var(--ink)",
                  background:
                    (p.tier_access || []).includes(t)
                      ? "var(--banana)"
                      : "transparent",
                  color: (p.tier_access || []).includes(t)
                    ? "var(--ink)"
                    : "var(--ink-4)",
                  letterSpacing: "0.08em",
                }}
              >
                {t.toUpperCase()}
              </span>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", paddingTop: 4 }}>
          <button type="button" className="btn sm" onClick={() => onEdit(p)}>
            Edit
          </button>
          <TestSuiteMenu
            onPing={() => onTest(p)}
            onRunSuite={(scope, modelId) => onRunSuite(p, scope, modelId)}
            models={(p.supported_models || [])
              .filter((m) => m.enabled)
              .map((m) => m.model_id)}
            defaultModel={(p.supported_models || []).find((m) => m.enabled)?.model_id}
          />
          <button type="button" className="btn sm" onClick={() => onTopup(p)}>
            Top-up ¥
          </button>
          {(p.circuit_state === "open" || p.circuit_state === "half_open") && (
            <button
              type="button"
              className="btn sm"
              onClick={() => onResetCircuit(p)}
            >
              Reset circuit
            </button>
          )}
          <button
            type="button"
            className="btn sm"
            onClick={() => onDelete(p)}
            style={{ marginLeft: "auto", color: "var(--bad)" }}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Topup mini-modal
// ---------------------------------------------------------------------------

function TopupModal({ provider, onClose, onSaved }) {
  const [amount, setAmount] = useState("10.00");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async () => {
    const n = Number(amount);
    if (!Number.isFinite(n) || n <= 0) {
      setError("amount must be > 0");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await adminProviders.topupProvider(provider.id, n);
      onSaved(res);
    } catch (err) {
      setError(err.message || "Top-up failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
      }}
      onClick={() => !busy && onClose()}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(360px, 100%)",
          background: "var(--paper)",
          border: "2px solid var(--ink)",
          boxShadow: "5px 5px 0 var(--ink)",
          padding: 22,
        }}
      >
        <div className="display" style={{ fontSize: 22, fontWeight: 800 }}>
          Top-up {provider.label}
        </div>
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
          current balance · ¥{Number(provider.balance_cny).toFixed(2)}
        </div>
        <div style={{ marginTop: 14 }}>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", marginBottom: 4 }}
          >
            amount_cny
          </div>
          <input
            className="inp"
            type="number"
            step="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            style={{ fontFamily: "var(--font-mono)" }}
          />
        </div>
        {error && (
          <div
            style={{
              marginTop: 8,
              padding: 8,
              border: "1px solid var(--bad)",
              color: "var(--bad)",
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 16,
          }}
        >
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary shadowed"
            onClick={submit}
            disabled={busy}
          >
            {busy ? "Crediting…" : "Credit"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Test result modal
// ---------------------------------------------------------------------------

function TestResultModal({ provider, result, busy, error, onClose, onRerun }) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
      }}
      onClick={() => !busy && onClose()}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(440px, 100%)",
          background: "var(--paper)",
          border: "2px solid var(--ink)",
          boxShadow: "5px 5px 0 var(--ink)",
          padding: 22,
        }}
      >
        <div className="display" style={{ fontSize: 22, fontWeight: 800 }}>
          Test {provider.label}
        </div>
        <div
          className="mono"
          style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}
        >
          POST /api/admin/providers/{provider.id}/test · ledger untouched
        </div>

        {busy && (
          <div
            className="mono"
            style={{ marginTop: 14, fontSize: 12, color: "var(--ink-3)" }}
          >
            probing upstream…
          </div>
        )}

        {error && !busy && (
          <div
            style={{
              marginTop: 14,
              padding: 8,
              border: "1px solid var(--bad)",
              color: "var(--bad)",
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}

        {result && !busy && (
          <div
            style={{
              marginTop: 14,
              padding: 12,
              border: `1px solid ${result.ok ? "var(--ok)" : "var(--bad)"}`,
              background: "var(--paper-2)",
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 10, fontWeight: 700, color: result.ok ? "var(--ok)" : "var(--bad)" }}
            >
              {result.ok ? "OK" : "FAILED"} · {result.model_id}
            </div>
            <div
              className="mono"
              style={{ fontSize: 12, marginTop: 6 }}
            >
              latency · {result.latency_ms.toFixed(1)}ms
            </div>
            <div className="mono" style={{ fontSize: 12 }}>
              images · {result.image_count}
            </div>
            {!result.ok && (
              <div
                className="mono"
                style={{ fontSize: 11, color: "var(--bad)", marginTop: 6 }}
              >
                {result.error_kind}: {result.error_message}
              </div>
            )}
          </div>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 16,
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={onClose}
            disabled={busy}
          >
            Close
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={onRerun}
            disabled={busy}
          >
            {busy ? "Probing…" : "Run again"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab
// ---------------------------------------------------------------------------

const REFRESH_INTERVAL_MS = 8000;

export default function ProvidersTab() {
  const [providers, setProviders] = useState([]);
  const [adapters, setAdapters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // dialogs
  const [editorMode, setEditorMode] = useState(null); // null | "create" | "edit"
  const [editTarget, setEditTarget] = useState(null);
  const [topupTarget, setTopupTarget] = useState(null);

  // test ping state
  const [testTarget, setTestTarget] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [testBusy, setTestBusy] = useState(false);
  const [testError, setTestError] = useState(null);

  // test-suite drawer state — only one suite can run at a time
  const [suiteRun, setSuiteRun] = useState(null); // { provider, scope, modelId }

  const refresh = useCallback(async () => {
    try {
      const [list, adapterList] = await Promise.all([
        adminProviders.listProviders(),
        adminProviders.listAdapters(),
      ]);
      setProviders(list || []);
      setAdapters(adapterList || []);
      setError(null);
    } catch (err) {
      setError(err.message || "Failed to load providers.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Auto-refresh so live metrics stay reasonably current. Light enough
  // (every 8s) that it doesn't matter; backend list is tiny.
  useEffect(() => {
    const t = setInterval(() => {
      void refresh();
    }, REFRESH_INTERVAL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const openCreate = () => {
    setEditTarget(null);
    setEditorMode("create");
  };

  const openEdit = (p) => {
    setEditTarget(p);
    setEditorMode("edit");
  };

  const onResetCircuit = async (p) => {
    try {
      await adminProviders.resetCircuit(p.id);
      await refresh();
    } catch (err) {
      setError(err.message || "Reset failed.");
    }
  };

  const onDelete = async (p) => {
    const ok = window.confirm(
      `Delete provider ${p.id}? This cannot be undone.\nBalance: ¥${Number(
        p.balance_cny,
      ).toFixed(2)}`,
    );
    if (!ok) return;
    try {
      await adminProviders.deleteProvider(p.id);
      await refresh();
    } catch (err) {
      setError(err.message || "Delete failed.");
    }
  };

  const runTest = async (p) => {
    setTestTarget(p);
    setTestResult(null);
    setTestError(null);
    setTestBusy(true);
    try {
      const r = await adminProviders.testProvider(p.id);
      setTestResult(r);
    } catch (err) {
      setTestError(err.message || "Test failed.");
    } finally {
      setTestBusy(false);
    }
  };

  const runSuite = (p, scope, modelId) => {
    if (suiteRun) {
      setError("已有测试在运行,请先关闭当前测试再启动新的。");
      return;
    }
    const fallbackModel =
      modelId ||
      (p.supported_models || []).find((m) => m.enabled)?.model_id ||
      null;
    if (!fallbackModel) {
      setError(`Provider ${p.id} 没有启用的模型,无法测试。`);
      return;
    }
    setError(null);
    setSuiteRun({ provider: p, scope, modelId: fallbackModel });
  };

  const closeSuite = () => {
    setSuiteRun(null);
    void refresh();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: 16,
        }}
      >
        <div>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            /api/admin/providers
          </div>
          <div
            className="display"
            style={{
              fontSize: 32,
              fontWeight: 800,
              letterSpacing: "-0.025em",
              marginTop: 6,
            }}
          >
            Upstream relay{" "}
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
              fleet.
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn" onClick={refresh}>
            <Icon name="refresh" size={13} />
            Refresh
          </button>
          <button
            type="button"
            className="btn primary shadowed"
            onClick={openCreate}
          >
            <Icon name="plus" size={13} />
            Add provider
          </button>
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

      {loading && providers.length === 0 && (
        <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
          loading providers…
        </div>
      )}

      {!loading && providers.length === 0 && (
        <div
          style={{
            padding: 22,
            border: "1px dashed var(--ink-4)",
            background: "var(--paper-2)",
            color: "var(--ink-3)",
            fontStyle: "italic",
            fontFamily: "var(--font-display)",
          }}
        >
          No providers yet — add one above.
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {providers.map((p) => (
          <ProviderCard
            key={p.id}
            p={p}
            onEdit={openEdit}
            onTest={runTest}
            onRunSuite={runSuite}
            onTopup={setTopupTarget}
            onResetCircuit={onResetCircuit}
            onDelete={onDelete}
          />
        ))}
      </div>

      {/* ---------- registered adapters ---------- */}
      <div>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            marginBottom: 10,
          }}
        >
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            REGISTERED ADAPTERS · /api/admin/adapters
          </div>
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            read-only · auto-discovered from disk
          </span>
        </div>
        <Hair thick />
        <div
          style={{
            marginTop: 12,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
          }}
        >
          {adapters.map((a) => (
            <div
              key={a.adapter_type}
              style={{
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                padding: 14,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
                  {a.adapter_type}
                </div>
                <span className="chip">
                  {(a.in_use_by_providers || []).length} provider(s)
                </span>
              </div>
              <div style={{ fontSize: 13, fontWeight: 600, marginTop: 4 }}>
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
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 8 }}
              >
                supports: {(a.supported_models || []).join(", ")}
              </div>
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 4 }}
              >
                in use by: {(a.in_use_by_providers || []).join(", ") || "—"}
              </div>
            </div>
          ))}
        </div>
      </div>

      {editorMode && (
        <ProviderEditorDialog
          mode={editorMode}
          provider={editTarget}
          adapters={adapters}
          onClose={() => {
            setEditorMode(null);
            setEditTarget(null);
          }}
          onSaved={() => {
            setEditorMode(null);
            setEditTarget(null);
            void refresh();
          }}
        />
      )}

      {topupTarget && (
        <TopupModal
          provider={topupTarget}
          onClose={() => setTopupTarget(null)}
          onSaved={() => {
            setTopupTarget(null);
            void refresh();
          }}
        />
      )}

      {testTarget && (
        <TestResultModal
          provider={testTarget}
          result={testResult}
          busy={testBusy}
          error={testError}
          onClose={() => {
            setTestTarget(null);
            setTestResult(null);
            setTestError(null);
          }}
          onRerun={() => runTest(testTarget)}
        />
      )}

      {suiteRun && (
        <ProviderTestSuiteDrawer
          provider={suiteRun.provider}
          scope={suiteRun.scope}
          modelId={suiteRun.modelId}
          onClose={closeSuite}
        />
      )}
    </div>
  );
}
