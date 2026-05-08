import PaginationPager from "./PaginationPager.jsx";

/**
 * ArchiveSetDetail
 * ------------------------------------------------------------------
 * 点击 ArchiveSetCard 后的展开详情视图。
 *
 * 包含：
 *   - 顶部面包屑 (← ARCHIVE / SET #1427) 和右侧元信息
 *   - 完整 prompt 框；其中以 <b>01.</b> 等数字标记的子提示词会以 banana-soft
 *     高亮显示（视觉上把"主提示+子提示"的结构写出来）
 *   - 4 列（≤880px 时 2 列）的 panel 网格 — 每张图独立卡，左上角带 01-0N 编号
 *
 * Props:
 *   id           作业编号
 *   model        模型名 (如 "gpt-image-2")
 *   age          相对时间 (如 "2m ago")
 *   panelCount   面板总数；不传则用 panels.length
 *   prompt       prompt 主体；可传字符串或 React 节点。
 *   panels       Array<{ src?, bg?, title?, starred?, state? }>
 *                每张图一个 panel；state 用法同 ArchiveSetCard:
 *                  done | loading | running | fail
 *   onPanelClick (panel, globalIndex) => void  — 点击 panel 时触发
 *   focusedIndex 当前聚焦 panel 的全局 index（用于在抽屉中导航时高亮）
 *   focusedSub   面包屑中显示的小标签（如 `panel 02`），抽屉关闭时传 null
 *   onBack       返回归档主页回调（点击面包屑左侧）
 *   pageSize     P3 — 单页 panel 数；超过此阈值时显示分页器（默认 12）
 *   page         当前页码（1-based）；不传则不分页
 *   onPageChange (nextPage) => void
 *   className    额外 className
 */
export default function ArchiveSetDetail({
  id,
  model = "gpt-image-2",
  age,
  panelCount,
  prompt,
  panels = [],
  onPanelClick,
  focusedIndex = null,
  focusedSub = null,
  onBack,
  className = "",
  pageSize = 12,
  page = null,
  onPageChange,
}) {
  const total = panelCount ?? panels.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const isPaged = page != null && total > pageSize;
  const safePage = isPaged ? Math.min(Math.max(1, page), totalPages) : 1;
  const start = isPaged ? (safePage - 1) * pageSize : 0;
  const end = isPaged ? Math.min(start + pageSize, total) : panels.length;
  const visible = isPaged ? panels.slice(start, end) : panels;

  return (
    <div className={`arch-detail-frame ${className}`.trim()}>
      <div className="arch-detail-top">
        <div className="arch-breadcrumb">
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              onBack && onBack();
            }}
          >
            ← ARCHIVE
          </a>
          <span className="arch-sep">/</span>
          <span className="arch-here">SET #{id}</span>
          {focusedSub ? (
            <>
              <span className="arch-sep">/</span>
              <span className="arch-here">{focusedSub}</span>
            </>
          ) : null}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span>
            {model} · {total} panels{age ? ` · ${age}` : ""}
          </span>
          {isPaged && (
            <PaginationPager
              current={safePage}
              total={totalPages}
              onPrev={() => onPageChange?.(Math.max(1, safePage - 1))}
              onNext={() => onPageChange?.(Math.min(totalPages, safePage + 1))}
            />
          )}
        </div>
      </div>

      <div className="arch-prompt-box">
        <div className="arch-ph-label">PROMPT</div>
        <div className="arch-ph-body">{renderPrompt(prompt)}</div>
      </div>

      <div className="arch-panels">
        {visible.map((p, localIdx) => {
          const i = start + localIdx;
          const cellState = p?.state || "done";
          const picStyle = p?.src && cellState === "done"
            ? {
                backgroundImage: `url(${p.src})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }
            : p?.bg
            ? { background: p.bg }
            : undefined;
          const focusedClass = i === focusedIndex ? "arch-panel-focused" : "";
          const interactive = !!onPanelClick && cellState !== "fail";
          const handleClick = interactive
            ? () => onPanelClick(p, i)
            : undefined;
          const stateClass =
            cellState === "fail" ? "arch-cell-fail" :
            cellState === "running" || cellState === "loading" ? "arch-cell-running" :
            "";
          return (
            <div
              key={i}
              data-testid={`set-detail-panel-${i}`}
              data-panel-state={cellState}
              className={`arch-panel ${focusedClass}`.trim()}
              onClick={handleClick}
              style={interactive ? { cursor: "pointer" } : undefined}
            >
              <div
                className={`arch-pic ${stateClass}`.trim()}
                style={picStyle}
              >
                <div className="arch-panel-num">
                  {String(i + 1).padStart(2, "0")}
                </div>
                {cellState === "fail" && (
                  <div className="arch-cell-corner">!</div>
                )}
                {(cellState === "running" || cellState === "loading") && (
                  <span className="arch-spin" />
                )}
              </div>
              <div className="arch-pmeta">
                <span>{p?.title || ""}</span>
                {p?.starred && <span className="arch-star" />}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * 把 prompt 字符串中的 "01." / "02." 等子提示标记自动包成 <b>，
 * 视觉上呼应设计稿中以 banana-soft 高亮的结构。
 * 传入 ReactNode 时直接返回原内容，不做处理。
 */
function renderPrompt(prompt) {
  if (prompt == null) return null;
  if (typeof prompt !== "string") return prompt;

  // 匹配独立的 "0N." (N=1..9) 或 "NN." 标记
  const regex = /(\b\d{2}\.)/g;
  const parts = prompt.split(regex);
  return parts.map((part, i) =>
    regex.test(part) ? <b key={i}>{part}</b> : <span key={i}>{part}</span>
  );
}
