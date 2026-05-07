import { useEffect, useRef, useState } from "react";

const SCOPE_OPTIONS = [
  {
    scope: "A",
    label: "健康检查",
    hint: "A · 1 次生图",
    desc: "等价于旧 Test ping,通过 SSE 抽屉显示",
  },
  {
    scope: "AB",
    label: "参数生效",
    hint: "A+B · 5-12 次",
    desc: "验证 capability 中声明的每个参数",
  },
  {
    scope: "FULL",
    label: "完整套件",
    hint: "A+B+C+D · 8-15 次",
    desc: "包含错误路径与人工判定",
  },
  {
    scope: "DRY_RUN",
    label: "仅错误路径",
    hint: "D · 0 次",
    desc: "纯校验,不消耗任何上游额度",
  },
];

export default function TestSuiteMenu({ onPing, onRunSuite, models, defaultModel }) {
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState(defaultModel || (models && models[0]) || "");
  const wrapRef = useRef(null);

  // Resync the controlled <select> value when the upstream models prop
  // arrives asynchronously (parent fetches the provider list, then
  // re-renders with the actual models). Without this, the <select>
  // would show "" against a populated option list and onRunSuite would
  // fire with model="".
  useEffect(() => {
    if (model && models && models.includes(model)) return;
    if (defaultModel && (!models || models.includes(defaultModel))) {
      setModel(defaultModel);
    } else if (models && models.length > 0) {
      setModel(models[0]);
    }
  }, [defaultModel, models, model]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const fire = (scope) => {
    setOpen(false);
    if (scope === "PING") onPing();
    else onRunSuite(scope, model);
  };

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        className="btn sm"
        data-test="test-suite-menu"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        Test ▾
      </button>
      {open && (
        // Treat as a plain popover with regular <button>s — no
        // role="menu" because we don't implement the full ARIA menu
        // pattern (roving focus, arrow keys). Using semantic buttons
        // is fine for screen readers.
        <div
          className="ts-menu"
          data-test="test-suite-menu-pop"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            zIndex: 30,
            minWidth: 280,
            padding: 6,
            background: "var(--paper)",
            border: "2px solid var(--ink)",
            boxShadow: "5px 5px 0 var(--ink)",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {models && models.length > 1 && (
            <div style={{ padding: "4px 8px 6px" }}>
              <div
                className="mono caps"
                style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.12em" }}
              >
                Target model
              </div>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="inp"
                style={{ fontFamily: "var(--font-mono)", fontSize: 11, marginTop: 4 }}
              >
                {models.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          )}
          <button
            type="button"
            className="ts-menu-item"
            onClick={() => fire("PING")}
            data-test="test-suite-ping"
          >
            <span className="ts-menu-row">
              <span className="ts-menu-label">Test ping</span>
              <span className="ts-menu-hint">原版 · A · 1 次</span>
            </span>
            <span className="ts-menu-desc">维持兼容,弹原版结果对话框</span>
          </button>
          <hr style={{ border: "none", borderTop: "1px dashed var(--ink-4)" }} />
          {SCOPE_OPTIONS.map((opt) => (
            <button
              key={opt.scope}
              type="button"
              className="ts-menu-item"
              data-test={`test-suite-${opt.scope}`}
              onClick={() => fire(opt.scope)}
            >
              <span className="ts-menu-row">
                <span className="ts-menu-label">{opt.label}</span>
                <span className="ts-menu-hint">{opt.hint}</span>
              </span>
              <span className="ts-menu-desc">{opt.desc}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
