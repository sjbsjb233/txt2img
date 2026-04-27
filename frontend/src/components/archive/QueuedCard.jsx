import ArchiveCard from "./ArchiveCard.jsx";

/**
 * QueuedCard
 * ------------------------------------------------------------------
 * 队列等待中的归档卡片 — RUNNING 的变种。
 *
 * 设计要点：
 *   - 大字号显示队列位置 (#7) — 让用户一眼看到自己排第几
 *   - "of 12 in queue" 副标，或 "next up" 文案（位置=1 时）
 *   - 进度条以队列推进度填充（不是渲染进度）
 *   - chip 是 paper-on-ink 描边版，与 banana 的 RUNNING chip 区分
 *
 * Props:
 *   size       "default" | "big" | "grid"
 *   id         作业编号
 *   model      模型名
 *   ratio      画幅比
 *   position   当前队列位置 (>= 1)
 *   total      队列总长度（可选，position=1 时省略会显示 "next up"）
 *   eta        预计等待时间字符串 (如 "~ 1m 40s")
 *   progress   0..1 之间的进度比例（队列推进度）。可选，默认按 position/total 推算
 *   metaLeft   覆盖默认 meta 左侧文字（可选）
 *   metaRight  覆盖默认 meta 右侧文字（可选，默认 "pos N/M" 或 "next"）
 *   onClick    卡片点击回调
 *
 * 示例:
 *   <QueuedCard
 *     id={1422}
 *     model="flash"
 *     ratio="3:4"
 *     position={7}
 *     total={12}
 *     eta="~ 1m 40s"
 *   />
 */
export default function QueuedCard({
  size = "default",
  id,
  model,
  ratio,
  position = 1,
  total,
  eta,
  progress,
  metaLeft,
  metaRight,
  onClick,
  className,
}) {
  const isNext = position === 1;
  // 进度推算：position=1 时接近完成 (92%)；否则按队列剩余比例反推
  const computedProgress =
    progress != null
      ? progress
      : total
      ? Math.max(0, Math.min(1, (total - position + 1) / total))
      : isNext
      ? 0.92
      : 0.42;

  const left =
    metaLeft != null
      ? metaLeft
      : [id != null ? `#${id}` : null, model, ratio].filter(Boolean).join(" · ");
  const right =
    metaRight != null
      ? metaRight
      : isNext
      ? "next"
      : total
      ? `pos ${position}/${total}`
      : `pos ${position}`;

  return (
    <ArchiveCard
      size={size}
      metaLeft={left}
      metaRight={
        <>
          <span className="arch-blink" />
          {right}
        </>
      }
      metaTone="running"
      thumbClassName="arch-run-thumb"
      onClick={onClick}
      className={className}
    >
      {/* QUEUED chip — 描边 + 内部脉动方块 */}
      <span className="arch-chip-status arch-chip-queued">
        <span className="arch-glyph-q" />
        QUEUED
      </span>
      {/* 中央大字队列位置 */}
      <div className="arch-queue-stamp">
        <div className="arch-q-pos">
          <span className="arch-q-hash">#</span>
          <span className="arch-q-num">{position}</span>
        </div>
        <div className="arch-q-of">
          {isNext ? "next up" : total ? `of ${total} in queue` : "in queue"}
        </div>
        {eta && <div className="arch-q-eta">{eta}</div>}
        <div className="arch-q-bar">
          <span style={{ width: `${Math.round(computedProgress * 100)}%` }} />
        </div>
      </div>
    </ArchiveCard>
  );
}
