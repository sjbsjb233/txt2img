import ArchiveCard from "./ArchiveCard.jsx";

/**
 * FailCard
 * ------------------------------------------------------------------
 * 失败 / 拒绝（policy reject）的归档卡片。
 *
 * 设计要点：
 *   - 静态、无动画，但在缩略尺寸下信息依旧可读
 *   - 不大面积铺红：仅 meta 行的时间戳用 --bad
 *   - 主区是 135° 双纸色斜纹 + 56px 圆形 ✕ 标志
 *   - 失败原因 reason 控制在 6 词内，超出截断
 *   - retry 按钮采用 brutal-shadow（2px 偏移阴影），点击回调由调用方提供
 *
 * Props:
 *   size       "default" | "big" | "grid"
 *   id         作业编号
 *   model      模型名
 *   ratio      画幅比
 *   age        距离失败的相对时间（如 "2m"）
 *   label      标题文字 (默认 "failed"，policy 拒绝时用 "prompt blocked" 等)
 *   reason     失败原因副标 (如 "timeout · 60s")
 *   chipText   左上角小标 (默认 "FAIL"，可改 "REJECTED" 等)
 *   onRetry    重试回调；不传则不显示 retry 按钮（如 policy 拒绝场景）
 *   metaLeft   覆盖默认 meta 左侧
 *   metaRight  覆盖默认 meta 右侧（默认 "failed · {age}"）
 *   onClick    卡片点击回调
 *
 * 示例:
 *   <FailCard id={1417} model="flash" ratio="16:9" age="2m"
 *             label="generation failed" reason="safety filter · prompt rejected"
 *             onRetry={() => retry(1417)} />
 *
 *   // 安全策略拒绝（不允许重试）：
 *   <FailCard id={1415} model="flash" ratio="1:1" age="8m"
 *             chipText="REJECTED"
 *             label="prompt blocked"
 *             reason="policy · category 04"
 *             metaRight="rejected · 8m" />
 */
export default function FailCard({
  size = "default",
  id,
  model,
  ratio,
  age,
  label = "failed",
  reason,
  chipText = "FAIL",
  onRetry,
  metaLeft,
  metaRight,
  onClick,
  className,
}) {
  const left =
    metaLeft != null
      ? metaLeft
      : [id != null ? `#${id}` : null, model, ratio].filter(Boolean).join(" · ");
  const right = metaRight != null ? metaRight : age ? `failed · ${age}` : "failed";

  // 阻止 retry 按钮事件冒泡到卡片 onClick
  const handleRetry = (e) => {
    e.stopPropagation();
    onRetry && onRetry();
  };

  return (
    <ArchiveCard
      size={size}
      metaLeft={left}
      metaRight={right}
      metaTone="fail"
      thumbClassName="arch-fail-thumb"
      onClick={onClick}
      className={className}
    >
      {/* 左上角状态标签 */}
      <span className="arch-chip-fail-tl">{chipText}</span>
      {/* 中央 ✕ 标志 + 标题 + 原因 */}
      <div className="arch-fail-stamp">
        <div className="arch-fail-glyph" />
        <div className="arch-fail-label">{label}</div>
        {reason && <div className="arch-fail-reason">{reason}</div>}
      </div>
      {/* 重试按钮（可选） */}
      {onRetry && (
        <button
          type="button"
          className="arch-fail-retry"
          onClick={handleRetry}
          aria-label="Retry generation"
        >
          ↻ RETRY
        </button>
      )}
    </ArchiveCard>
  );
}
