import ArchiveCard from "./ArchiveCard.jsx";

/**
 * ArchiveSetCard
 * ------------------------------------------------------------------
 * 多图生成结果的聚合归档卡片（gpt-image-2 等会一次产出多张图的模型）。
 *
 * 一个 SET = 一个 tile：footprint 与单图卡完全一致，仅 thumb 内部为 contact sheet。
 * 触发条件: `model === 'gpt-image-2' && images.length > 1`
 *
 * 布局规则（按图片数量）:
 *   - N=1   → 调用方应改用单图卡，本组件不处理
 *   - N=2   → 1×2 横向分割
 *   - N=3   → 1 hero + 2 stacked (新闻版式)
 *   - N=4   → 2×2 contact sheet
 *   - N≥5   → 2×2，第 4 格盖 "+(N-3) more" overlay
 *
 * Props:
 *   size       "default" | "big" | "grid"
 *   id         作业编号 (用于 meta 行)
 *   model      模型名 (默认 "gpt-image-2")
 *   age        相对时间 (如 "2m")
 *   images     Array<{ src?: string, bg?: string, label?: string,
 *                      state?: "done"|"running"|"fail" }>
 *              - src      图片 URL；优先于 bg
 *              - bg       占位色 (任意 CSS color，没有 src 时用)
 *              - label    cell hover 时显示的标题（可选，仅 hover 提示用）
 *              - state    单 cell 状态 (用于 partial fail / still running)
 *   totalCount 集合实际总数；不传则用 images.length。
 *              当 totalCount > 4 时第 4 格自动盖 "+ N more" overlay
 *   running    整组仍在生成时设为 true，会显示右上角 RUNNING chip
 *   metaLeft   覆盖默认 meta 左侧
 *   metaRight  覆盖默认 meta 右侧
 *   onClick    点击卡片回调（通常用于打开详情视图）
 *
 * 示例 — 完成态 N=4:
 *   <ArchiveSetCard
 *     id={1427}
 *     model="gpt-image-2"
 *     age="2m"
 *     images={[
 *       { src: url1, label: "dawn interior" },
 *       { src: url2 }, { src: url3 }, { src: url4 },
 *     ]}
 *     onClick={() => openDetail(1427)}
 *   />
 *
 * 示例 — 仍在跑 (2 of 4 done):
 *   <ArchiveSetCard
 *     id={1395} age="22s" running
 *     metaRight="running · 22s"
 *     images={[
 *       { src: u1 }, { src: u2 },
 *       { state: "running" }, { state: "running" },
 *     ]}
 *   />
 */
export default function ArchiveSetCard({
  size = "default",
  id,
  model = "gpt-image-2",
  age,
  images = [],
  totalCount,
  running = false,
  metaLeft,
  metaRight,
  onClick,
  className,
}) {
  const total = totalCount ?? images.length;
  const n = Math.min(images.length, 4);
  // contact sheet 布局类
  const sheetClass = n === 2 ? "arch-n2" : n === 3 ? "arch-n3" : "";

  // 当总数 > 4，第 4 格显示 "+N more"
  const overflow = total > 4 ? total - 3 : 0;

  const visible = images.slice(0, 4);

  const left = metaLeft != null ? metaLeft : `#${id} · ${model}`;
  const right = metaRight != null ? metaRight : age;

  return (
    <ArchiveCard
      size={size}
      metaLeft={left}
      metaRight={right}
      onClick={onClick}
      className={className}
    >
      {/* 顶部 SET 徽章 — 一直保持在左上角 */}
      <span className="arch-set-badge">
        <span className="arch-set-dot" />
        SET · {total}
      </span>

      {/* 右上角 RUNNING chip — 仅在整组仍在跑时显示 */}
      {running && (
        <span className="arch-chip-running-set">
          <span className="arch-glyph" />
          RUNNING
        </span>
      )}

      {/* contact sheet — 1px ink 间隙形成十字分隔线 */}
      <div className={`arch-sheet ${sheetClass}`.trim()}>
        {visible.map((img, i) => {
          const isLast = i === 3;
          const showMore = isLast && overflow > 0;
          const cellState = img?.state || "done";

          // 单 cell 仍在生成
          if (cellState === "running") {
            return (
              <div key={i} className="arch-cell arch-cell-running">
                <span className="arch-spin" />
              </div>
            );
          }

          // cell 背景：优先 src，再 bg
          const cellStyle = img?.src
            ? {
                backgroundImage: `url(${img.src})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }
            : img?.bg
            ? { background: img.bg }
            : undefined;

          const failClass = cellState === "fail" ? "arch-cell-fail" : "";

          return (
            <div
              key={i}
              className={`arch-cell ${failClass}`.trim()}
              style={cellStyle}
            >
              {/* partial fail 时的右上角红角标 */}
              {cellState === "fail" && <div className="arch-cell-corner">!</div>}

              {/* +N more overlay — 只在 N>4 的最后一格 */}
              {showMore && (
                <div className="arch-more-overlay">
                  <div className="arch-more-plus">+{overflow}</div>
                  <div className="arch-more-label">more</div>
                </div>
              )}

              {/* hover 时显示的 cell 标签（如果传了 label） */}
              {img?.label && cellState === "done" && (
                <div className="arch-cell-tooltip">
                  <span className="arch-cell-tooltip-num">
                    {String(i + 1).padStart(2, "0")} ·
                  </span>
                  {img.label}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </ArchiveCard>
  );
}
