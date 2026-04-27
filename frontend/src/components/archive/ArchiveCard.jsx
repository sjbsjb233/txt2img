/**
 * ArchiveCard
 * ------------------------------------------------------------------
 * 归档页（History / Archive）网格中所有卡片的通用骨架。
 *
 * 与单图、SET、RUNNING、QUEUED、FAIL 共享同一个外壳：
 *   - 1px ink 边框 + 米白底 (#fffdf7)
 *   - 顶部 thumb 区域 (1:1 默认)
 *   - 底部 mono 字体 meta 行
 * 状态特异的视觉差异完全由 thumb 内的子组件承担，外壳从不变。
 * 这样保证 archive 网格在不同状态混排时节奏不被打断。
 *
 * Props:
 *   size       "default" | "big" | "grid"
 *                - default → 280px 固定宽，适合独立展示
 *                - big     → 360px 固定宽，详情/特写用
 *                - grid    → 跟随父级 grid 宽度（width:auto），常用
 *   metaLeft   meta 行左侧文字（如 "#1421 · flash · 1:1"）
 *   metaRight  meta 行右侧文字（如 "06s"）
 *   metaTone   "default" | "running" | "fail" — 控制 meta 行颜色基调
 *   onClick    点击整张卡片回调（meta 行也可点）
 *   className  额外 className（拼接到根元素）
 *   children   thumb 区域要渲染的内容（chip / overlay / 图片等）
 *
 * 示例:
 *   <ArchiveCard
 *     size="grid"
 *     metaLeft="#1421 · flash · 1:1"
 *     metaRight="2m"
 *     onClick={...}
 *   >
 *     <img src={url} className="arch-thumb-img" />
 *   </ArchiveCard>
 */
export default function ArchiveCard({
  size = "default",
  metaLeft,
  metaRight,
  metaTone = "default",
  onClick,
  className = "",
  thumbClassName = "",
  children,
}) {
  const sizeClass =
    size === "big" ? "arch-card-big" : size === "grid" ? "arch-card-grid" : "";
  const metaToneClass =
    metaTone === "running"
      ? "arch-meta-running"
      : metaTone === "fail"
      ? "arch-meta-fail"
      : "";

  return (
    <div
      className={`arch-card ${sizeClass} ${className}`.trim()}
      onClick={onClick}
      style={onClick ? { cursor: "pointer" } : undefined}
    >
      <div className={`arch-thumb ${thumbClassName}`.trim()}>{children}</div>
      {(metaLeft != null || metaRight != null) && (
        <div className={`arch-meta ${metaToneClass}`.trim()}>
          <span>{metaLeft}</span>
          <span className="arch-meta-ago">{metaRight}</span>
        </div>
      )}
    </div>
  );
}
