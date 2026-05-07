import { Fragment, useEffect, useReducer, useRef } from "react";
import {
  postManualVerdict,
  startTestSuite,
} from "../../api/admin/providerTestSuite.js";
import CaseCard from "./CaseCard.jsx";
import ImageLightbox from "./ImageLightbox.jsx";

const SCOPE_TO_BODY = {
  A: { suites: ["A"] },
  AB: { suites: ["A", "B"] },
  FULL: { suites: ["A", "B", "C", "D"] },
  DRY_RUN: { suites: ["D"], dry_run: true },
};

const SUITE_LABELS = {
  A: "A · 健康检查",
  B: "B · 参数生效",
  C: "C · 视觉特性",
  D: "D · 错误路径",
};

const initialState = {
  status: "starting", // starting | running | done | aborted | error
  ordering: [],
  cases: {}, // case_id -> { ... }
  totals: { pass: 0, warn: 0, fail: 0, skipped: 0 },
  budgetUsed: 0,
  budgetPlanned: 0,
  totalCases: 0,
  startTime: 0,
  verdict: null,
  errorMsg: null,
  lightbox: null,
  runId: null,
  skippedByCapability: [],
};

function reducer(state, action) {
  switch (action.type) {
    case "run_start":
      return {
        ...state,
        status: "running",
        runId: action.run_id,
        totalCases: action.total_cases,
        budgetPlanned: action.total_cost_planned,
        skippedByCapability: action.skipped_by_capability || [],
        startTime: Date.now(),
      };
    case "case_start": {
      const ordering = state.ordering.includes(action.case_id)
        ? state.ordering
        : [...state.ordering, action.case_id];
      return {
        ...state,
        status: "running",
        ordering,
        cases: {
          ...state.cases,
          [action.case_id]: {
            case_id: action.case_id,
            suite: action.suite,
            title: action.title,
            params: action.params || {},
            judge_level: action.judge_level,
            cost_image: action.cost_image,
            expect_error: action.expect_error,
            images: [],
            auto_verdict: [],
            status: "running",
          },
        },
      };
    }
    case "case_image": {
      const c = state.cases[action.case_id];
      if (!c) return state;
      const updated = {
        ...c,
        images: [
          ...c.images,
          {
            idx: action.idx,
            name: action.name,
            mime: action.mime,
            width: action.width,
            height: action.height,
            byte_size: action.byte_size,
            bytes_url: action.bytes_url,
          },
        ],
      };
      return {
        ...state,
        cases: { ...state.cases, [action.case_id]: updated },
        budgetUsed: state.budgetUsed + (c.cost_image ? 1 : 0),
      };
    }
    case "case_result": {
      const c = state.cases[action.case_id];
      if (!c) return state;
      const judgeLevel = c.judge_level;
      const status =
        action.ok && (judgeLevel === "SEMI" || judgeLevel === "MANUAL")
          ? "manual_pending"
          : action.ok
          ? "pass"
          : "fail";
      const updated = {
        ...c,
        status,
        auto_verdict: action.auto_verdict || [],
        manual_prompt: action.manual_prompt,
        error_kind: action.error_kind,
        error_message: action.error_message,
        latency_ms: action.latency_ms,
        ok: action.ok,
        cost_image: action.cost_image,
        // Replace any optimistic image array with the canonical one when the
        // server includes a richer description in case_result.
        images:
          action.images && action.images.length
            ? action.images.map((img) => ({
                idx: img.idx,
                name: img.name,
                mime: img.mime,
                width: img.width,
                height: img.height,
                byte_size: img.byte_size,
                bytes_url: img.bytes_url,
              }))
            : c.images,
      };
      const totals = { ...state.totals };
      if (status === "pass") totals.pass += 1;
      else if (status === "fail") totals.fail += 1;
      else if (status === "manual_pending") totals.warn += 1;
      return {
        ...state,
        cases: { ...state.cases, [action.case_id]: updated },
        totals,
      };
    }
    case "manual_verdict": {
      const c = state.cases[action.case_id];
      if (!c) return state;
      const totals = { ...state.totals };
      if (c.status === "manual_pending") totals.warn = Math.max(0, totals.warn - 1);
      const newStatus =
        action.verdict === "pass"
          ? "pass"
          : action.verdict === "fail"
          ? "fail"
          : "skipped";
      if (newStatus === "pass") totals.pass += 1;
      else if (newStatus === "fail") totals.fail += 1;
      else totals.skipped += 1;
      return {
        ...state,
        cases: {
          ...state.cases,
          [action.case_id]: { ...c, status: newStatus, manual_verdict: action.verdict },
        },
        totals,
      };
    }
    case "run_done":
      return {
        ...state,
        status: "done",
        verdict: action.verdict,
        budgetUsed: action.cost_used ?? state.budgetUsed,
      };
    case "abort":
      return { ...state, status: "aborted" };
    case "error":
      return { ...state, status: "error", errorMsg: action.message };
    case "lightbox_open":
      return { ...state, lightbox: action.payload };
    case "lightbox_close":
      return { ...state, lightbox: null };
    default:
      return state;
  }
}

function computeOverallVerdict(state) {
  if (state.status === "error") return "FAIL";
  if (state.status !== "done") return null;
  // Use server-provided verdict but recompute locally so manual changes
  // after the run finished still flow into the badge.
  const cases = Object.values(state.cases);
  if (cases.some((c) => c.status === "fail")) return "FAIL";
  if (cases.some((c) => c.status === "manual_pending")) return "WARN";
  return "PASS";
}

export default function ProviderTestSuiteDrawer({ provider, scope, modelId, onClose }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const connRef = useRef(null);

  useEffect(() => {
    if (!provider || !scope || !modelId) return;
    const body = { ...SCOPE_TO_BODY[scope], model_id: modelId };
    const conn = startTestSuite(provider.id, body);
    connRef.current = conn;
    const onStart = (e) => dispatch({ type: "run_start", ...e.detail });
    const onCaseStart = (e) => dispatch({ type: "case_start", ...e.detail });
    const onImage = (e) => dispatch({ type: "case_image", ...e.detail });
    const onResult = (e) => dispatch({ type: "case_result", ...e.detail });
    const onDone = (e) => dispatch({ type: "run_done", ...e.detail });
    const onError = (e) =>
      dispatch({ type: "error", message: e.detail?.message || "stream error" });
    conn.on("run_start", onStart);
    conn.on("case_start", onCaseStart);
    conn.on("case_image", onImage);
    conn.on("case_result", onResult);
    conn.on("run_done", onDone);
    conn.on("error", onError);
    return () => {
      try {
        conn.abort();
      } catch {
        /* ignore */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider?.id, scope, modelId]);

  const onStop = () => {
    if (connRef.current) {
      try {
        connRef.current.abort();
      } catch {
        /* ignore */
      }
    }
    dispatch({ type: "abort" });
  };

  const requestClose = () => {
    const pending = Object.values(state.cases).filter((c) => c.status === "manual_pending");
    if (pending.length > 0 && state.status !== "aborted" && state.status !== "error") {
      const ok = window.confirm(
        `还有 ${pending.length} 条用例待人工判定,确认关闭?\n关闭后总评保持 WARN.`,
      );
      if (!ok) return;
    }
    onClose();
  };

  const onManual = async (caseId, verdict) => {
    dispatch({ type: "manual_verdict", case_id: caseId, verdict });
    if (state.runId) {
      try {
        await postManualVerdict(provider.id, state.runId, caseId, verdict);
      } catch {
        /* UI already updated; audit row will be missing — non-blocking */
      }
    }
  };

  const verdict = computeOverallVerdict(state);
  const ordered = state.ordering.map((id) => state.cases[id]).filter(Boolean);

  // Group cases by suite for the heading dividers.
  let lastSuite = null;
  const groupedRendered = ordered.map((c) => {
    const showHeader = c.suite !== lastSuite;
    lastSuite = c.suite;
    return (
      <Fragment key={c.case_id}>
        {showHeader && (
          <div
            className="mono caps"
            style={{
              fontSize: 10,
              color: "var(--ink-3)",
              letterSpacing: "0.16em",
              margin: "16px 0 6px",
            }}
          >
            {SUITE_LABELS[c.suite] || c.suite}
          </div>
        )}
        <CaseCard
          caseState={c}
          onLightbox={(img) => dispatch({ type: "lightbox_open", payload: img })}
          onManual={(v) => onManual(c.case_id, v)}
        />
      </Fragment>
    );
  });

  const completed = ordered.filter((c) => c.status !== "running").length;
  const progressPct =
    state.totalCases > 0 ? Math.min(100, Math.round((completed / state.totalCases) * 100)) : 0;

  return (
    <div
      data-test="test-suite-drawer"
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(720px, 100vw)",
        background: "var(--paper)",
        borderLeft: "2px solid var(--ink)",
        boxShadow: "-5px 0 0 var(--ink)",
        zIndex: 40,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "14px 18px",
          borderBottom: "1px solid var(--ink)",
          background: "var(--paper-2)",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
              {provider.label}
            </div>
            <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              {provider.id} · {provider.adapter_type} · {modelId} · 套件 {scope}
            </div>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {state.status === "running" && (
              <button
                type="button"
                className="btn sm"
                onClick={onStop}
                data-test="test-suite-stop"
              >
                停止
              </button>
            )}
            <button
              type="button"
              className="btn sm"
              onClick={requestClose}
              data-test="test-suite-close"
            >
              关闭
            </button>
          </div>
        </div>

        {/* Progress */}
        <div
          style={{
            marginTop: 10,
            height: 6,
            background: "var(--paper-3)",
            border: "1px solid var(--ink)",
            position: "relative",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              bottom: 0,
              width: `${progressPct}%`,
              background: "var(--banana)",
              transition: "width 200ms linear",
            }}
          />
        </div>
        <div className="mono" style={{ marginTop: 6, fontSize: 11, color: "var(--ink-3)" }}>
          {completed}/{state.totalCases || "?"} cases · 已生图 {state.budgetUsed}
          {state.budgetPlanned > 0 ? ` / 预算 ${state.budgetPlanned}` : ""}
        </div>
      </div>

      {/* Summary */}
      <div
        style={{
          padding: "10px 18px",
          borderBottom: "1px solid var(--ink-4)",
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 8,
          background: "var(--paper)",
        }}
      >
        {[
          { label: "通过", v: state.totals.pass, color: "var(--ok)" },
          { label: "待人工", v: state.totals.warn, color: "var(--banana-deep)" },
          { label: "失败", v: state.totals.fail, color: "var(--bad)" },
          { label: "跳过", v: state.totals.skipped, color: "var(--ink-3)" },
        ].map((m) => (
          <div
            key={m.label}
            data-test={`summary-${m.label}`}
            style={{
              border: "1px solid var(--ink-4)",
              padding: "6px 10px",
              background: "var(--paper-2)",
              textAlign: "center",
            }}
          >
            <div
              className="mono caps"
              style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.14em" }}
            >
              {m.label}
            </div>
            <div
              className="mono"
              style={{
                fontSize: 18,
                fontWeight: 700,
                color: m.color,
                marginTop: 2,
              }}
            >
              {m.v}
            </div>
          </div>
        ))}
      </div>

      {/* Case list */}
      <div style={{ flex: 1, overflow: "auto", padding: "8px 18px 18px" }}>
        {state.status === "starting" && (
          <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 12 }}>
            建立连接中…
          </div>
        )}
        {state.status === "error" && state.errorMsg && (
          <div
            data-test="suite-error-banner"
            style={{
              padding: 10,
              border: "1px solid var(--bad)",
              color: "var(--bad)",
              fontSize: 12,
              marginTop: 8,
            }}
          >
            连接错误: {state.errorMsg}
          </div>
        )}
        {groupedRendered}

        {state.skippedByCapability && state.skippedByCapability.length > 0 && (
          <div
            className="mono"
            style={{
              marginTop: 12,
              fontSize: 11,
              color: "var(--ink-3)",
              fontStyle: "italic",
            }}
          >
            未触发的能力:capability 未声明,自动跳过 {state.skippedByCapability.join(", ")}
          </div>
        )}
      </div>

      {/* Footer */}
      {(state.status === "done" || state.status === "aborted") && (
        <div
          style={{
            padding: "12px 18px",
            borderTop: "1px solid var(--ink)",
            background: "var(--paper-2)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
          data-test="test-suite-footer"
        >
          <span
            className="mono caps"
            style={{
              fontSize: 11,
              fontWeight: 700,
              padding: "4px 10px",
              border: "1px solid var(--ink)",
              letterSpacing: "0.12em",
              background:
                verdict === "PASS"
                  ? "var(--ok)"
                  : verdict === "WARN"
                  ? "var(--banana)"
                  : "var(--bad)",
              color: verdict === "WARN" ? "var(--ink)" : "var(--paper)",
            }}
          >
            {verdict || "—"}
          </span>
          <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
            {state.status === "aborted" ? "已中止" : `共 ${ordered.length} 个用例`}
            {state.startTime > 0 ? ` · 用时 ${Math.round((Date.now() - state.startTime) / 1000)}s` : ""}
          </span>
          <button
            type="button"
            className="btn sm"
            style={{ marginLeft: "auto" }}
            onClick={requestClose}
          >
            完成
          </button>
        </div>
      )}

      <ImageLightbox
        image={state.lightbox}
        onClose={() => dispatch({ type: "lightbox_close" })}
      />
    </div>
  );
}
