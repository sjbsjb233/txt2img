import ArchiveCard from "./ArchiveCard.jsx";

/**
 * RunningCard
 * ------------------------------------------------------------------
 * 渲染中（in-flight）的归档卡片。
 *
 * 设计要点：
 *   - 只用 paper / ink，不用彩色 → 不抢已完成图片的注意力
 *   - 多重低强度动画叠加（影线漂移 / 扫描线 / 进度条 / chip 旋转 / 心跳点）
 *     共同营造"还在跑"的语义，单看每一处都很安静
 *   - chip 用 banana 是这块状态唯一的彩色，只在 in-flight 时使用
 *
 * Props:
 *   size       "default" | "big" | "grid"
 *   id         作业编号 (用于 meta 显示，如 "#1421")
 *   model      模型名 (如 "flash")
 *   ratio      画幅比 (如 "1:1")
 *   seconds    已运行秒数 — 数字或字符串，组件不负责自增计时
 *              （由调用方维护一个 setInterval / state 后传入）
 *   metaLeft   覆盖默认拼接的 meta 左侧文字（可选）
 *   onClick    卡片点击回调
 *
 * 示例:
 *   const [t, setT] = useState(0);
 *   useEffect(() => { const i = setInterval(() => setT(s => s + 1), 1000);
 *     return () => clearInterval(i); }, []);
 *   <RunningCard size="grid" id={1421} model="flash" ratio="1:1" seconds={t} />
 */
export default function RunningCard({
  size = "default",
  id,
  model,
  ratio,
  seconds = 0,
  metaLeft,
  onClick,
  className,
}) {
  const secLabel =
    typeof seconds === "number"
      ? `${String(seconds).padStart(2, "0")}s`
      : seconds;

  const left =
    metaLeft != null
      ? metaLeft
      : [id != null ? `#${id}` : null, model, ratio].filter(Boolean).join(" · ");

  return (
    <ArchiveCard
      size={size}
      metaLeft={left}
      metaRight={
        <>
          <span className="arch-blink" />
          {secLabel}
        </>
      }
      metaTone="running"
      thumbClassName="arch-run-thumb"
      onClick={onClick}
      className={className}
    >
      {/* 软色块呼吸 — 让 thumb 不死板 */}
      <div className="arch-run-bgshift" />
      {/* 中央 caption — "RENDERING 14s" */}
      <div className="arch-run-caption">
        rendering<span className="arch-run-seconds">{secLabel}</span>
      </div>
      {/* 状态 chip — banana 背景 + 旋转半圆 */}
      <span className="arch-chip-status arch-chip-running">
        <span className="arch-glyph" />
        RUNNING
      </span>
      {/* 底部不确定进度条 */}
      <div className="arch-run-progress" />
    </ArchiveCard>
  );
}
