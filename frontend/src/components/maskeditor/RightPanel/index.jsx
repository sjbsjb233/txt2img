import BrushPanel from "./BrushPanel.jsx";
import SelectPanel from "./SelectPanel.jsx";
import PromptPanel from "./PromptPanel.jsx";
import MaskOpsRow from "./MaskOpsRow.jsx";
import OutpaintToolPanel from "./OutpaintToolPanel.jsx";
import LineageGraphPanel from "../lineage/LineageGraphPanel.jsx";
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
  refs,
  onAddRef,
  onRemoveRef,
  advanced,
  setAdvanced,
  onOp,
  lastOp,
  sourceThumbUrl,
  outpaintMode,
  outpaint,
  setOutpaint,
  imageW,
  imageH,
  sourceHashId,
  sourceOrder,
  maskMethod = "native",
  templateOpen = false,
  setTemplateOpen,
  templateLocked = true,
  setTemplateLocked,
  customTemplate = null,
  setCustomTemplate,
}) {
  // Tabs differ between inpaint and outpaint modes (PRD §5.8).
  const tabs = outpaintMode
    ? [
        { id: "outpaint", label: "outpaint" },
        { id: "prompt", label: "prompt" },
        { id: "history", label: "history" },
      ]
    : [
        { id: "tool", label: "tool" },
        { id: "prompt", label: "prompt" },
        { id: "history", label: "history" },
      ];

  // Resolve a valid active tab for the current mode — switching mode
  // shouldn't leave the active id pointing at a tab that no longer
  // exists for that mode.
  const validIds = new Set(tabs.map((t) => t.id));
  const activeTab = validIds.has(tab) ? tab : tabs[0].id;

  return (
    <div className="me-right-panel" data-testid="me-right-panel">
      <div className="me-tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`me-tab ${activeTab === t.id ? "me-tab--active" : ""}`}
            onClick={() => setTab(t.id)}
            data-testid={`me-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="me-tab-content">
        {activeTab === "tool" && !outpaintMode && (
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
        {activeTab === "outpaint" && outpaintMode && (
          <>
            <SectionHeader>outpaint</SectionHeader>
            <OutpaintToolPanel
              outpaint={outpaint}
              setOutpaint={setOutpaint}
              imageW={imageW}
              imageH={imageH}
            />
          </>
        )}
        {activeTab === "prompt" && (
          <>
            <SectionHeader>prompt &amp; references</SectionHeader>
            <PromptPanel
              prompt={prompt}
              setPrompt={setPrompt}
              refs={refs}
              onAddRef={onAddRef}
              onRemoveRef={onRemoveRef}
              advanced={advanced}
              setAdvanced={setAdvanced}
              sourceThumbUrl={sourceThumbUrl}
              maskMethod={maskMethod}
              outpaintMode={outpaintMode}
              templateOpen={templateOpen}
              setTemplateOpen={setTemplateOpen}
              templateLocked={templateLocked}
              setTemplateLocked={setTemplateLocked}
              customTemplate={customTemplate}
              setCustomTemplate={setCustomTemplate}
            />
          </>
        )}
        {activeTab === "history" && (
          <LineageGraphPanel
            currentHashId={sourceHashId}
            currentOrder={sourceOrder || 1}
          />
        )}
      </div>
      {!outpaintMode && <MaskOpsRow onOp={onOp} lastOp={lastOp} />}
    </div>
  );
}
