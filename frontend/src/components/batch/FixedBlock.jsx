import Icon from "../Icon.jsx";

export default function FixedBlock({
  prompt,
  appendMode,
  refs,
  onPromptChange,
  onAppendModeChange,
  onAddRef,
  onRemoveRef,
}) {
  return (
    <div
      data-testid="fixed-block"
      style={{
        border: "2px solid var(--ink)",
        background: "#fffdf7",
        boxShadow: "4px 4px 0 var(--ink)",
        position: "relative",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: -1,
          left: -1,
          padding: "3px 9px",
          background: "var(--ink)",
          color: "var(--banana)",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.1em",
        }}
      >
        § FIXED — APPLIED TO EVERY SLOT
      </div>

      <div
        style={{
          padding: "26px 16px 10px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          borderBottom: "1px solid var(--rule-2)",
        }}
      >
        <span
          className="mono caps"
          style={{ fontSize: 10, color: "var(--ink-3)" }}
        >
          Append mode
        </span>
        <div style={{ display: "flex", border: "1px solid var(--ink)" }}>
          {["prepend", "append"].map((m, i) => (
            <button
              key={m}
              type="button"
              onClick={() => onAppendModeChange?.(m)}
              data-testid={`append-mode-${m}`}
              style={{
                padding: "4px 10px",
                background:
                  appendMode === m ? "var(--banana)" : "#fffdf7",
                border: "none",
                borderLeft: i ? "1px solid var(--ink)" : "none",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              {m === "prepend" ? "fixed → slot" : "slot → fixed"}
            </button>
          ))}
        </div>
        <span
          className="mono"
          style={{ fontSize: 10, color: "var(--ink-4)" }}
        >
          {appendMode === "prepend"
            ? "fixed prompt prepends, refs come first"
            : "slot prompt prepends, fixed refs come first"}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: 0 }}>
        <div
          style={{
            padding: "12px 16px",
            borderRight: "1px solid var(--rule-2)",
          }}
        >
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-4)",
              marginBottom: 6,
            }}
          >
            Fixed prompt
          </div>
          <textarea
            data-testid="fixed-prompt"
            value={prompt || ""}
            onChange={(e) => onPromptChange?.(e.target.value)}
            placeholder="empty · type the style anchor that should apply to every slot"
            rows={3}
            style={{
              width: "100%",
              border: "1px solid var(--rule-2)",
              outline: "none",
              resize: "vertical",
              fontFamily: "var(--font-display)",
              fontSize: 16,
              lineHeight: 1.4,
              color: "var(--ink)",
              padding: "10px 12px",
              background: "var(--paper)",
              letterSpacing: "-0.005em",
            }}
          />
          <div
            className="mono"
            style={{
              fontSize: 9,
              color: "var(--ink-4)",
              marginTop: 6,
            }}
          >
            prepended to every slot's prompt · {(prompt || "").length} chars
          </div>
        </div>
        <div style={{ padding: "12px 14px", background: "var(--paper)" }}>
          <div
            className="mono caps"
            style={{
              fontSize: 9,
              color: "var(--ink-4)",
              marginBottom: 6,
            }}
          >
            Fixed refs · {refs.length} / 14
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(5, 1fr)",
              gap: 4,
            }}
          >
            {refs.map((file, i) => (
              <button
                type="button"
                key={i}
                title={file.name}
                onClick={() => onRemoveRef?.(i)}
                style={{
                  aspectRatio: "1/1",
                  background: "var(--paper-2)",
                  border: "1px solid var(--ink)",
                  position: "relative",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  padding: 0,
                }}
              >
                <span
                  className="mono"
                  style={{
                    position: "absolute",
                    bottom: 1,
                    right: 2,
                    fontSize: 8,
                    color: "rgba(0,0,0,0.7)",
                  }}
                >
                  {i + 1}
                </span>
                <span
                  className="mono"
                  style={{
                    fontSize: 9,
                    color: "var(--ink-3)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    width: "80%",
                  }}
                >
                  {file.name?.slice(0, 6) || "ref"}
                </span>
              </button>
            ))}
            <label
              style={{
                aspectRatio: "1/1",
                border: "1px dashed var(--ink-3)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--ink-4)",
                cursor: "pointer",
              }}
            >
              <Icon name="plus" size={12} stroke="var(--ink-4)" />
              <input
                type="file"
                accept="image/*"
                multiple
                style={{ display: "none" }}
                onChange={(e) => {
                  const files = Array.from(e.target.files || []);
                  files.forEach((f) => onAddRef?.(f));
                  e.target.value = "";
                }}
              />
            </label>
          </div>
        </div>
      </div>
    </div>
  );
}
