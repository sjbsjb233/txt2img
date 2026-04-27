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
 *   prompt       prompt 主体；可传字符串或 React 节点。如果传字符串，
 *                组件会自动把 "01." / "02." 等子提示标记包成 <b> 高亮。
 *   panels       Array<{ src?, bg?, title?, starred? }>
 *                每张图一个 panel，title 是 panel 下方说明字
 *   onBack       返回归档主页回调（点击面包屑左侧）
 *   className    额外 className
 *
 * 示例:
 *   <ArchiveSetDetail
 *     id={1427}
 *     model="gpt-image-2"
 *     age="2m ago"
 *     prompt={`A four-panel storyboard of a coffee shop opening day —
 *       01. empty interior at dawn, warm yellow tones.
 *       02. close-up of barista grinding beans, terracotta.
 *       03. forest light through the window, deep green.
 *       04. packed cafe at peak hour, cobalt rush.`}
 *     panels={[
 *       { src: u1, title: "dawn interior", starred: true },
 *       { src: u2, title: "barista close-up" },
 *       { src: u3, title: "morning light" },
 *       { src: u4, title: "peak hour" },
 *     ]}
 *     onBack={() => navigate("/archive")}
 *   />
 */
export default function ArchiveSetDetail({
  id,
  model = "gpt-image-2",
  age,
  panelCount,
  prompt,
  panels = [],
  onBack,
  className = "",
}) {
  const total = panelCount ?? panels.length;

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
        </div>
        <div>
          {model} · {total} panels{age ? ` · ${age}` : ""}
        </div>
      </div>

      <div className="arch-prompt-box">
        <div className="arch-ph-label">PROMPT</div>
        <div className="arch-ph-body">{renderPrompt(prompt)}</div>
      </div>

      <div className="arch-panels">
        {panels.map((p, i) => {
          const picStyle = p?.src
            ? {
                backgroundImage: `url(${p.src})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }
            : p?.bg
            ? { background: p.bg }
            : undefined;
          return (
            <div key={i} className="arch-panel">
              <div className="arch-pic" style={picStyle}>
                <div className="arch-panel-num">
                  {String(i + 1).padStart(2, "0")}
                </div>
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
