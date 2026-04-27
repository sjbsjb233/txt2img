import { useState, useEffect, useRef, useLayoutEffect } from "react";
import Icon from "../components/Icon.jsx";
import {
  RunningCard,
  QueuedCard,
  FailCard,
  ArchiveSetCard,
} from "../components/archive";

const HX_IMGS = Array.from({ length: 24 }, (_, i) => {
  const palette = [
    "#9bdac5",
    "#3f6a26",
    "#cf6638",
    "#3a3631",
    "#f0c2db",
    "#9bdac5",
    "#f5c862",
    "#3f73c8",
    "#cf6638",
    "#efe9d9",
    "#5b2418",
    "#bedb98",
  ];
  // 状态分布展示新组件:
  //   SET     — 多图聚合卡 (gpt-image-2)
  //   QUEUED  — 队列等待
  //   FAIL    — 渲染失败
  //   RUNNING — 渲染中
  //   BEST    — 已完成的精选 (沿用旧实现)
  const statuses = [
    "", "", "SET", "", "FAIL", "RUNNING", "BEST", "",
    "QUEUED", "", "", "", "SET", "", "", "",
  ];
  const models = ["pro-2×", "flash", "pro-2×", "draft"];
  const ratios = ["1:1", "16:9", "3:4", "4:3"];
  return {
    c: palette[i % palette.length],
    s: statuses[i % statuses.length],
    id: 1428 - i,
    model: models[i % 4],
    ratio: ratios[i % 4],
    ago: `${Math.floor(i / 2) + 1}m`,
    prompt: [
      "a still-life of three overripe bananas on a marble slab, flemish painting, warm window light, shallow depth of field, 35mm",
      "cyberpunk alley, neon reflection in puddle, hasselblad, cinematic, 8k",
      "portrait of an old farmer, shot on hasselblad, natural light, tack sharp",
      "minimal banana branding, cream background, soft shadow, studio product shot",
      "forest canopy from below, golden hour, wide-angle, cathedral light",
      "brutalist building, concrete & banana yellow, architectural photography",
    ][i % 6],
  };
});

// SET 卡片用的 placeholder 图片色板 (4 张为一组)
const SET_PALETTES = [
  ["#d9a534", "#c25b30", "#4a6a2e", "#2f5bb7"],
  ["#9bdac5", "#f0c2db", "#2f2c28", "#e3dac5"],
];

function FilterPopover({ onClose, onPick }) {
  const ref = useRef();
  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    const t = setTimeout(() => document.addEventListener("mousedown", onDoc), 0);
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const props = [
    { id: "period", label: "Period", key: "P", glyph: <Icon name="clock" size={12} /> },
    {
      id: "model",
      label: "Model",
      key: "M",
      glyph: (
        <span
          style={{
            width: 14,
            height: 14,
            background: "var(--ink)",
            color: "var(--banana)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 9,
            fontWeight: 800,
            fontFamily: "var(--font-mono)",
          }}
        >
          M
        </span>
      ),
    },
    {
      id: "shape",
      label: "Shape",
      key: "A",
      glyph: (
        <span
          style={{ width: 14, height: 14, border: "1.5px solid var(--ink)", display: "inline-block" }}
        />
      ),
    },
    {
      id: "status",
      label: "Status",
      key: "S",
      glyph: (
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: "var(--banana-deep)",
            display: "inline-block",
            margin: "0 3px",
          }}
        />
      ),
    },
    { id: "rating", label: "Rating", key: "R", glyph: <span style={{ fontSize: 12, color: "var(--ink)" }}>★</span> },
    { id: "session", label: "Session", key: "N", glyph: <Icon name="grid" size={12} /> },
  ];

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        top: "calc(100% + 8px)",
        left: 0,
        zIndex: 50,
        width: 340,
        background: "#fffdf7",
        border: "1px solid var(--ink)",
        boxShadow: "5px 5px 0 var(--ink)",
        padding: 16,
        animation: "popIn 140ms cubic-bezier(.2,.9,.3,1)",
      }}
    >
      <style>{`
        @keyframes popIn { from { opacity: 0; transform: translateY(-4px) scale(.98); } to { opacity: 1; transform: none; } }
      `}</style>
      <div style={{ position: "relative" }}>
        <input
          className="inp"
          placeholder="search filters..."
          style={{
            paddingLeft: 28,
            fontFamily: "var(--font-mono)",
            fontStyle: "italic",
            color: "var(--ink-3)",
          }}
        />
        <div style={{ position: "absolute", top: 11, left: 8, pointerEvents: "none" }}>
          <Icon name="search" size={12} stroke="var(--ink-3)" />
        </div>
      </div>
      <div
        className="mono caps"
        style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 14, marginBottom: 4 }}
      >
        PROPERTIES
      </div>
      {props.map((p) => (
        <button
          key={p.id}
          onClick={() => {
            onPick(p.id);
            onClose();
          }}
          style={{
            display: "grid",
            gridTemplateColumns: "20px 1fr 16px",
            gap: 10,
            alignItems: "center",
            width: "100%",
            padding: "8px 4px",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            textAlign: "left",
          }}
        >
          <span style={{ display: "inline-flex" }}>{p.glyph}</span>
          <span style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600 }}>
            {p.label}
          </span>
          <span
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-3)", textAlign: "right" }}
          >
            {p.key}
          </span>
        </button>
      ))}
    </div>
  );
}

function JobDrawer({ item, onClose, onPrev, onNext, width }) {
  const open = !!item;
  const [render, setRender] = useState(false);

  useEffect(() => {
    if (open) setRender(true);
    else {
      const t = setTimeout(() => setRender(false), 360);
      return () => clearTimeout(t);
    }
  }, [open]);

  useEffect(() => {
    const onKey = (e) => {
      if (!open) return;
      if (e.key === "Escape") onClose();
      if (e.key === "[") onPrev?.();
      if (e.key === "]") onNext?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, onPrev, onNext]);

  if (!render && !open) return null;
  const it = item || {};

  return (
    <aside
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width,
        background: "var(--paper)",
        borderLeft: "1px solid var(--ink)",
        transform: open ? "translateX(0)" : "translateX(100%)",
        transition: "transform 360ms cubic-bezier(.22,.85,.22,1)",
        zIndex: 30,
        display: "flex",
        flexDirection: "column",
        boxShadow: open ? "-12px 0 32px -16px #19171455" : "none",
        willChange: "transform",
      }}
    >
      <div
        style={{
          padding: "14px 18px",
          borderBottom: "1px solid var(--ink)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <div
          className="mono caps"
          style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}
        >
          JOB DETAIL
        </div>
        <span className="display" style={{ fontSize: 16, fontWeight: 800 }}>
          #{it.id}
        </span>
        <span
          style={{ width: 10, height: 10, background: "var(--ok)", display: "inline-block" }}
        />
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          ⌘[{" "}
          <button
            onClick={onPrev}
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "inherit",
              fontFamily: "inherit",
              fontSize: "inherit",
            }}
          >
            prev
          </button>{" "}
          ·{" "}
          <button
            onClick={onNext}
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "inherit",
              fontFamily: "inherit",
              fontSize: "inherit",
            }}
          >
            ⌘]
          </button>
        </span>
        <button
          onClick={onClose}
          style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4 }}
        >
          <Icon name="close" size={14} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "20px 22px" }}>
        <div
          style={{
            aspectRatio: "16/11",
            background: it.c || "#9bdac5",
            border: "1px solid var(--ink)",
            position: "relative",
          }}
        >
          <button
            style={{
              position: "absolute",
              top: 8,
              right: 8,
              padding: "4px 10px",
              background: "var(--ink)",
              color: "var(--paper)",
              border: "none",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            ↗ open
          </button>
        </div>

        <div style={{ marginTop: 16, display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button className="btn sm ink" style={{ padding: "0 12px" }}>
            <Icon name="download" size={11} stroke="var(--banana)" />
            <span style={{ color: "var(--banana)" }}>download</span>
          </button>
          <button className="btn sm">★ pick</button>
          <button className="btn sm">↻ retry</button>
          <button className="btn sm">⌥ recreate</button>
          <button className="btn sm">↗ share</button>
          <button
            style={{
              background: "transparent",
              border: "none",
              padding: "0 10px",
              cursor: "pointer",
              fontFamily: "var(--font-sans)",
              fontSize: 12,
              color: "var(--bad)",
              fontWeight: 600,
            }}
          >
            delete
          </button>
        </div>

        <div style={{ marginTop: 22 }}>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            PROMPT
          </div>
          <div
            style={{
              marginTop: 8,
              padding: "12px 14px",
              background: "var(--paper-2)",
              border: "1px solid var(--ink)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              lineHeight: 1.55,
              position: "relative",
            }}
          >
            {it.prompt || "—"}
            <button
              style={{
                position: "absolute",
                bottom: 6,
                right: 8,
                background: "transparent",
                border: "none",
                cursor: "pointer",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "var(--ink-3)",
              }}
            >
              copy
            </button>
          </div>
        </div>

        <div style={{ marginTop: 22 }}>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            META
          </div>
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}>
            {[
              ["model", it.model === "pro-2×" ? "ChatGPT Images 2.0" : it.model || "—"],
              ["shape", `${it.ratio || "1:1"} · 2048×2048`],
              ["created", "Wed 24 Apr · 14:32:08"],
              ["duration", "14.2s"],
              ["session", "Editorial cover →", true],
            ].map(([k, v, link]) => (
              <div
                key={k}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "6px 0",
                  borderBottom: "1px dashed var(--rule-2)",
                }}
              >
                <span style={{ color: "var(--ink-3)" }}>{k}</span>
                <span
                  style={{
                    fontWeight: 600,
                    color: link ? "var(--info)" : "var(--ink)",
                    textDecoration: link ? "underline" : "none",
                  }}
                >
                  {v}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ marginTop: 22 }}>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)", letterSpacing: "0.16em" }}
          >
            RELATED
          </div>
          <div
            style={{
              marginTop: 10,
              display: "grid",
              gridTemplateColumns: "repeat(5, 1fr)",
              gap: 6,
            }}
          >
            {["#9bdac5", "#e8a98a", "#3a3631", "#f0c2db", "#7fb6e3"].map((c, i) => (
              <div
                key={i}
                style={{
                  aspectRatio: "1/1",
                  background: c,
                  border: "1px solid var(--ink)",
                }}
              />
            ))}
          </div>
          <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 10 }}>
            + 19 in same session
          </div>
        </div>
      </div>
    </aside>
  );
}

export default function ArchivePage() {
  const [period, setPeriod] = useState("7d");
  const [filterOpen, setFilterOpen] = useState(false);
  const [drawerItem, setDrawerItem] = useState(null);
  const drawerOpen = !!drawerItem;

  const baseCols = 4;
  const cols = drawerOpen ? Math.max(3, baseCols - 1) : baseCols;
  const drawerWidth = 460;

  const titleWrap = useRef(null);
  const titleEl = useRef(null);
  const [titleScale, setTitleScale] = useState(1);

  useLayoutEffect(() => {
    let raf;
    const measure = () => {
      if (!titleWrap.current || !titleEl.current) return;
      titleEl.current.style.transform = "scale(1)";
      const w = titleWrap.current.clientWidth;
      const natural = titleEl.current.scrollWidth;
      const s = natural > w ? Math.max(0.4, w / natural) : 1;
      setTitleScale(s);
    };
    raf = requestAnimationFrame(measure);
    const ro = new ResizeObserver(() => requestAnimationFrame(measure));
    if (titleWrap.current) ro.observe(titleWrap.current);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [drawerOpen]);

  const items = HX_IMGS;
  const open = (item) => setDrawerItem(item);
  const close = () => setDrawerItem(null);
  const idx = drawerItem ? items.findIndex((x) => x.id === drawerItem.id) : -1;
  const prev = () => idx > 0 && setDrawerItem(items[idx - 1]);
  const next = () => idx >= 0 && idx < items.length - 1 && setDrawerItem(items[idx + 1]);

  // RUNNING 卡片需要一个秒数计时器以显示已运行时间。
  // 这里用一个全局 ticker 给所有 RUNNING 卡片共享 — 真实场景下应该按
  // 任务起始时间分别计算 (Date.now() - job.startedAt)。
  const [runTick, setRunTick] = useState(0);
  useEffect(() => {
    const i = setInterval(() => setRunTick((t) => t + 1), 1000);
    return () => clearInterval(i);
  }, []);

  return (
    <div
      style={{
        flex: 1,
        overflow: "hidden",
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}
    >
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          paddingRight: drawerOpen ? drawerWidth : 0,
          transition: "padding-right 360ms cubic-bezier(.22,.85,.22,1)",
          willChange: "padding-right",
        }}
      >
        <div style={{ padding: "32px 56px 60px" }}>
          <div
            className="mono caps"
            style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.18em" }}
          >
            HISTORY · ARCHIVE
          </div>

          <div ref={titleWrap} style={{ marginTop: 6, overflow: "hidden", paddingBottom: 2 }}>
            <h1
              ref={titleEl}
              className="display"
              style={{
                fontSize: 42,
                fontWeight: 900,
                letterSpacing: "-0.035em",
                lineHeight: 1.15,
                margin: 0,
                whiteSpace: "nowrap",
                display: "inline-block",
                transform: `scale(${titleScale})`,
                transformOrigin: "left center",
                transition: "transform 360ms cubic-bezier(.22,.85,.22,1)",
                willChange: "transform",
              }}
            >
              Everything you've made.
            </h1>
          </div>

          <div
            style={{
              marginTop: 14,
              display: "flex",
              alignItems: "center",
              gap: 14,
              flexWrap: "nowrap",
              minHeight: 40,
            }}
          >
            <div
              style={{
                position: "relative",
                display: "flex",
                alignItems: "center",
                gap: 10,
                flexShrink: 0,
              }}
            >
              <button
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "8px 14px",
                  borderRadius: 999,
                  background: "var(--ink)",
                  color: "var(--paper)",
                  border: "1px solid var(--ink)",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                period <span style={{ fontWeight: 800 }}>{period}</span>
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    setPeriod("∞");
                  }}
                  style={{ marginLeft: 4, opacity: 0.7, cursor: "pointer" }}
                >
                  ×
                </span>
              </button>

              <div style={{ position: "relative" }}>
                <button
                  onClick={() => setFilterOpen(!filterOpen)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "8px 14px",
                    borderRadius: 999,
                    background: "transparent",
                    color: "var(--ink-3)",
                    border: "1px dashed var(--ink-3)",
                    cursor: "pointer",
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                  }}
                >
                  + filter
                </button>
                {filterOpen && (
                  <FilterPopover onClose={() => setFilterOpen(false)} onPick={() => {}} />
                )}
              </div>
            </div>

            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 8,
                marginLeft: 4,
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              <span
                className="ticker"
                style={{ fontSize: 22, fontWeight: 900, letterSpacing: "-0.03em" }}
              >
                {items.length * 27}
              </span>
              <span className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
                matches
              </span>
              {!drawerOpen && (
                <span
                  style={{
                    fontFamily: "var(--font-display)",
                    fontStyle: "italic",
                    fontSize: 14,
                    color: "var(--ink-3)",
                  }}
                >
                  over 7 days
                </span>
              )}
            </div>

            <div
              style={{
                marginLeft: "auto",
                display: "flex",
                alignItems: "center",
                gap: 10,
                flexShrink: 0,
              }}
            >
              <select
                className="inp"
                style={{
                  height: 32,
                  width: 100,
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  background: "#fffdf7",
                }}
              >
                <option>newest ▾</option>
                <option>oldest</option>
              </select>
              <div style={{ display: "flex", border: "1px solid var(--ink)" }}>
                <button
                  style={{
                    width: 28,
                    height: 32,
                    background: "var(--banana)",
                    border: "none",
                    borderRight: "1px solid var(--ink)",
                    cursor: "pointer",
                  }}
                />
                <button
                  style={{
                    width: 28,
                    height: 32,
                    background: "#fffdf7",
                    border: "none",
                    borderRight: "1px solid var(--ink)",
                    cursor: "pointer",
                  }}
                />
                <button
                  style={{
                    width: 28,
                    height: 32,
                    background: "#fffdf7",
                    border: "none",
                    cursor: "pointer",
                  }}
                />
              </div>
            </div>
          </div>

          <div
            style={{
              marginTop: 28,
              display: "grid",
              gridTemplateColumns: `repeat(${cols}, 1fr)`,
              gap: 12,
              transition: "grid-template-columns 360ms cubic-bezier(.22,.85,.22,1)",
            }}
          >
            {items.map((img) => {
              const focused = drawerItem?.id === img.id;
              const focusClass = focused ? "arch-focused" : "";

              // —— RUNNING — 用 RunningCard 替代旧的简易 chip
              if (img.s === "RUNNING") {
                return (
                  <RunningCard
                    key={img.id}
                    size="grid"
                    id={img.id}
                    model={img.model}
                    ratio={img.ratio}
                    seconds={runTick}
                    onClick={() => open(img)}
                    className={focusClass}
                  />
                );
              }

              // —— QUEUED — 队列等待
              if (img.s === "QUEUED") {
                return (
                  <QueuedCard
                    key={img.id}
                    size="grid"
                    id={img.id}
                    model={img.model}
                    ratio={img.ratio}
                    position={3}
                    total={8}
                    eta="~ 48s"
                    onClick={() => open(img)}
                    className={focusClass}
                  />
                );
              }

              // —— FAIL — 静态失败卡 + retry 按钮
              if (img.s === "FAIL") {
                return (
                  <FailCard
                    key={img.id}
                    size="grid"
                    id={img.id}
                    model={img.model}
                    ratio={img.ratio}
                    age={img.ago}
                    label="failed"
                    reason="timeout · 60s"
                    onRetry={() => {
                      // TODO: hook to backend retry endpoint
                      console.log("retry", img.id);
                    }}
                    onClick={() => open(img)}
                    className={focusClass}
                  />
                );
              }

              // —— SET — 多图聚合卡 (gpt-image-2 风格)
              if (img.s === "SET") {
                const palette = SET_PALETTES[img.id % SET_PALETTES.length];
                return (
                  <ArchiveSetCard
                    key={img.id}
                    size="grid"
                    id={img.id}
                    model="gpt-image-2"
                    age={img.ago}
                    images={palette.map((bg) => ({ bg }))}
                    totalCount={4}
                    onClick={() => open(img)}
                    className={focusClass}
                  />
                );
              }

              // —— 默认 / BEST — 沿用原有简易卡片实现
              return (
                <div
                  key={img.id}
                  onClick={() => open(img)}
                  style={{
                    border: focused ? "2px solid var(--bad)" : "1px solid var(--ink)",
                    background: "#fffdf7",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    transition:
                      "border-color 120ms, transform 360ms cubic-bezier(.22,.85,.22,1)",
                    willChange: "transform",
                  }}
                >
                  <div style={{ aspectRatio: "1/1", background: img.c, position: "relative" }}>
                    {img.s === "BEST" && (
                      <span
                        style={{
                          position: "absolute",
                          top: 6,
                          right: 6,
                          width: 22,
                          height: 22,
                          background: "var(--banana)",
                          border: "1px solid var(--ink)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: 13,
                          fontWeight: 900,
                        }}
                      >
                        ★
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      padding: "8px 10px",
                      borderTop: "1px solid var(--ink)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: "var(--ink-2)",
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <span>
                      #{img.id} · {img.model} · {img.ratio}
                    </span>
                    <span style={{ color: "var(--ink-3)" }}>{img.ago}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <JobDrawer
        item={drawerItem}
        onClose={close}
        onPrev={prev}
        onNext={next}
        width={drawerWidth}
      />
    </div>
  );
}
