import { useRef } from "react";
import { Field, FieldLabel, Seg, Toggle } from "./atoms.jsx";
import {
  PROMPT_PRESETS,
  FALLBACK_TEMPLATE_INPAINT,
  FALLBACK_TEMPLATE_OUTPAINT,
} from "../../../config/maskEdit.js";
import MEIcon from "../MEIcon.jsx";
import MaskMethodBar from "./MaskMethodBar.jsx";
import FallbackTemplateBlock from "./FallbackTemplateBlock.jsx";

export default function PromptPanel({
  prompt,
  setPrompt,
  refs = [],
  onAddRef,
  onRemoveRef,
  advanced,
  setAdvanced,
  sourceThumbUrl,
  maskMethod = "native",
  outpaintMode = false,
  templateOpen = false,
  setTemplateOpen,
  templateLocked = true,
  setTemplateLocked,
  customTemplate = null,
  setCustomTemplate,
}) {
  const promptRef = useRef(null);
  function insertPreset(text) {
    const ta = promptRef.current;
    if (!ta) return;
    const start = ta.selectionStart || 0;
    const end = ta.selectionEnd || 0;
    const before = prompt.slice(0, start);
    const after = prompt.slice(end);
    const newValue = before + text + after;
    setPrompt(newValue);
    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange((before + text).length, (before + text).length);
    });
  }
  const defaultTemplate = outpaintMode
    ? FALLBACK_TEMPLATE_OUTPAINT
    : FALLBACK_TEMPLATE_INPAINT;
  const modified = customTemplate != null && customTemplate !== defaultTemplate;
  return (
    <div className="me-panel-body" data-testid="me-prompt-panel">
      <MaskMethodBar
        method={maskMethod}
        expanded={templateOpen}
        onToggle={() => setTemplateOpen?.(!templateOpen)}
      />
      {maskMethod === "fallback" && templateOpen && (
        <FallbackTemplateBlock
          value={customTemplate}
          defaultValue={defaultTemplate}
          locked={templateLocked}
          modified={modified}
          onChange={(v) => setCustomTemplate?.(v)}
          onUnlock={() => setTemplateLocked?.(false)}
          onLock={() => setTemplateLocked?.(true)}
          onReset={() => {
            setCustomTemplate?.(null);
          }}
        />
      )}
      <Field label="prompt" extra={`${prompt.length} / 32000`}>
        <textarea
          ref={promptRef}
          className="inp"
          rows={5}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          style={{ resize: "none", lineHeight: 1.5 }}
          placeholder="describe the change you want…"
          data-testid="me-prompt"
        />
      </Field>

      <div style={{ marginBottom: 14 }}>
        <FieldLabel>presets</FieldLabel>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {PROMPT_PRESETS.map((p) => (
            <span
              key={p.id}
              className="chip"
              style={{ fontSize: 10, cursor: "pointer", padding: "3px 8px" }}
              onClick={() => insertPreset(p.insert)}
            >
              {p.label}
            </span>
          ))}
        </div>
      </div>

      <div style={{ borderTop: "1px solid var(--rule)", margin: "16px -16px 12px", padding: "12px 16px 0" }}>
        <FieldLabel extra={`${refs.length + 1} / 16`}>references</FieldLabel>
        <div className="me-refs-grid">
          <div className="me-refs-thumb" title="source (locked)">
            {sourceThumbUrl && <img src={sourceThumbUrl} alt="source" />}
            <span className="me-refs-thumb__source">source</span>
          </div>
          {refs.map((r, i) => (
            <div
              key={i}
              className="me-refs-thumb"
              title={r.name}
              onClick={() => onRemoveRef?.(i)}
            >
              <img src={r.url} alt={r.name} />
            </div>
          ))}
          <label className="me-refs-add" title="add reference">
            <MEIcon name="plus" size={14} />
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onAddRef?.(f);
                e.target.value = "";
              }}
            />
          </label>
        </div>
      </div>

      <details>
        <summary
          className="me-field-label__name"
          style={{ cursor: "pointer", marginBottom: 8 }}
        >
          ▸ advanced
        </summary>
        <Field label="size">
          <Seg
            options={[
              { value: "sq", label: "1024²" },
              { value: "land", label: "1536×1024" },
              { value: "port", label: "1024×1536" },
              { value: "auto", label: "auto" },
            ]}
            value={advanced.size}
            onChange={(v) => setAdvanced({ ...advanced, size: v })}
            dense
          />
        </Field>
        <Field label="quality">
          <Seg
            options={["low", "medium", "high"]}
            value={advanced.quality}
            onChange={(v) => setAdvanced({ ...advanced, quality: v })}
            dense
          />
        </Field>
        <Field label="thinking">
          <Seg
            options={["off", "low", "med", "high"]}
            value={advanced.thinking}
            onChange={(v) => setAdvanced({ ...advanced, thinking: v })}
            dense
          />
        </Field>
        <Field label="output">
          <Seg
            options={["png", "jpeg", "webp"]}
            value={advanced.output}
            onChange={(v) => setAdvanced({ ...advanced, output: v })}
            dense
          />
        </Field>
        <Toggle
          on={advanced.streamPartial}
          label="stream partial previews"
          onChange={(v) => setAdvanced({ ...advanced, streamPartial: v })}
        />
      </details>
    </div>
  );
}
