import BrushPanel from "./BrushPanel.jsx";
import SelectPanel from "./SelectPanel.jsx";
import PromptPanel from "./PromptPanel.jsx";
import HistoryPanel from "./HistoryPanel.jsx";
import MaskOpsRow from "./MaskOpsRow.jsx";
import OutpaintRightPanel from "./OutpaintRightPanel.jsx";
import { SectionHeader } from "./atoms.jsx";

const TOOL_LABEL = {
  brush: "brush · paint to add",
  eraser: "eraser",
  rect: "rectangle marquee",
  lasso: "lasso",
  wand: "magic wand",
  pan: "pan",
  outpaint: "outpaint",
};

export default function RightPanel({
  tab,
  setTab,
  tool,
  brushOpts,
  setBrushOpts,
  prompt,
  setPrompt,
  negative,
  setNegative,
  refs,
  onAddRef,
  onRemoveRef,
  advanced,
  setAdvanced,
  history,
  onJumpHistory,
  onOp,
  lastOp,
  sourceThumbUrl,
  outpaintMode,
  outpaint,
  setOutpaint,
  imageW,
  imageH,
}) {
  if (outpaintMode) {
    return (
      <div className="me-right-panel" data-testid="me-right-panel">
        <OutpaintRightPanel
          outpaint={outpaint}
          setOutpaint={setOutpaint}
          imageW={imageW}
          imageH={imageH}
          prompt={prompt}
          setPrompt={setPrompt}
        />
        <MaskOpsRow onOp={onOp} lastOp={lastOp} />
      </div>
    );
  }

  const tabs = [
    { id: "tool", label: "tool" },
    { id: "prompt", label: "prompt" },
    { id: "history", label: "history" },
  ];
  return (
    <div className="me-right-panel" data-testid="me-right-panel">
      <div className="me-tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`me-tab ${tab === t.id ? "me-tab--active" : ""}`}
            onClick={() => setTab(t.id)}
            data-testid={`me-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="me-tab-content">
        {tab === "tool" && (
          <>
            <SectionHeader action={tool}>{TOOL_LABEL[tool] || tool}</SectionHeader>
            {tool === "brush" || tool === "eraser" ? (
              <BrushPanel brushOpts={brushOpts} setBrushOpts={setBrushOpts} />
            ) : (
              <SelectPanel
                tool={tool}
                brushOpts={brushOpts}
                setBrushOpts={setBrushOpts}
              />
            )}
          </>
        )}
        {tab === "prompt" && (
          <>
            <SectionHeader>prompt &amp; references</SectionHeader>
            <PromptPanel
              prompt={prompt}
              setPrompt={setPrompt}
              negative={negative}
              setNegative={setNegative}
              refs={refs}
              onAddRef={onAddRef}
              onRemoveRef={onRemoveRef}
              advanced={advanced}
              setAdvanced={setAdvanced}
              sourceThumbUrl={sourceThumbUrl}
            />
          </>
        )}
        {tab === "history" && (
          <>
            <SectionHeader action={`${history.length} / 50 steps`}>
              history
            </SectionHeader>
            <HistoryPanel items={history} onJump={onJumpHistory} />
          </>
        )}
      </div>
      <MaskOpsRow onOp={onOp} lastOp={lastOp} />
    </div>
  );
}
