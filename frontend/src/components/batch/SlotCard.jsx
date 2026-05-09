import Icon from "../Icon.jsx";

const IMAGE_PRESETS = [1, 2, 4, 8];

export default function SlotCard({
  slot,
  fixedRefsCount,
  onChange,
  onDuplicate,
  onDelete,
}) {
  const setField = (k, v) => onChange?.({ ...slot, [k]: v });

  return (
    <div
      data-testid="slot-card"
      data-stable-idx={slot.stable_idx}
      style={{
        border: "1px solid var(--ink)",
        background: "#fffdf7",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 12px",
          borderBottom: "1px solid var(--rule-2)",
        }}
      >
        <span
          className="mono"
          style={{
            padding: "2px 6px",
            background: "var(--ink)",
            color: "var(--banana)",
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: "0.06em",
            flexShrink: 0,
          }}
        >
          #{String(slot.stable_idx).padStart(2, "0")}
        </span>

        <input
          data-testid="slot-title"
          value={slot.title || ""}
          placeholder="Slot title"
          onChange={(e) => setField("title", e.target.value)}
          style={{
            flex: 1,
            minWidth: 0,
            border: "none",
            background: "transparent",
            outline: "none",
            fontFamily: "var(--font-display)",
            fontSize: 16,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            color: "var(--ink)",
          }}
        />

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexShrink: 0,
          }}
        >
          <span
            className="chip"
            style={{
              background: "var(--banana-soft)",
              border: "1px solid var(--ink)",
              padding: "1px 6px",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              fontWeight: 700,
            }}
          >
            ×{slot.image_count} IMG
          </span>
        </div>

        <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
          <button
            type="button"
            onClick={onDuplicate}
            title="Duplicate"
            data-testid="slot-duplicate"
            className="chip"
            style={{
              border: "1px solid var(--ink)",
              padding: "2px 6px",
              cursor: "pointer",
              background: "#fffdf7",
            }}
          >
            <Icon name="stack" size={10} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            title="Delete"
            data-testid="slot-delete"
            className="chip"
            style={{
              border: "1px solid var(--ink)",
              padding: "2px 6px",
              cursor: "pointer",
              background: "#fffdf7",
            }}
          >
            <Icon name="trash" size={10} />
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 220px", gap: 0 }}>
        <div
          style={{
            padding: "10px 14px",
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
            Prompt
          </div>
          <textarea
            data-testid="slot-prompt"
            value={slot.prompt || ""}
            placeholder="describe what this slot should generate"
            onChange={(e) => setField("prompt", e.target.value)}
            rows={3}
            style={{
              width: "100%",
              border: "none",
              outline: "none",
              resize: "vertical",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              lineHeight: 1.55,
              color: "var(--ink)",
              background: "transparent",
              padding: 0,
            }}
          />
        </div>
        <div
          style={{
            padding: "10px 12px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
            background: "var(--paper)",
          }}
        >
          <div>
            <div
              className="mono caps"
              style={{
                fontSize: 9,
                color: "var(--ink-4)",
                marginBottom: 4,
              }}
            >
              Images
            </div>
            <div
              style={{ display: "flex", border: "1px solid var(--ink)" }}
              data-testid="slot-image-count-buttons"
            >
              {IMAGE_PRESETS.map((n, i) => {
                const on = slot.image_count === n;
                return (
                  <button
                    type="button"
                    key={n}
                    onClick={() => setField("image_count", n)}
                    data-testid={`slot-image-${n}`}
                    style={{
                      flex: 1,
                      height: 24,
                      background: on ? "var(--banana)" : "#fffdf7",
                      border: "none",
                      borderLeft: i ? "1px solid var(--ink)" : "none",
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      fontWeight: 700,
                      cursor: "pointer",
                    }}
                  >
                    {n}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <div
              className="mono caps"
              style={{
                fontSize: 9,
                color: "var(--ink-4)",
                marginBottom: 4,
              }}
            >
              Set
            </div>
            <div
              className="mono"
              style={{
                padding: "3px 6px",
                border: "1px solid var(--ink)",
                background: "#fffdf7",
                fontSize: 10,
                color: "var(--ink-2)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {slot.set_id || "auto · per-submit"}
            </div>
          </div>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            <span
              className="mono"
              style={{ fontSize: 9, color: "var(--ink-4)" }}
            >
              inherits global
            </span>
          </div>
        </div>
      </div>

      <div
        style={{
          padding: "8px 12px",
          borderTop: "1px solid var(--rule-2)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span
          className="mono caps"
          style={{
            fontSize: 9,
            color: "var(--ink-4)",
            flexShrink: 0,
          }}
        >
          Refs
        </span>
        <div
          style={{ display: "flex", gap: 4, alignItems: "center" }}
        >
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-4)" }}
          >
            slot-only refs · prepended/appended to fixed
          </span>
        </div>
        <span style={{ flex: 1 }} />
        <span
          className="mono"
          style={{ fontSize: 9, color: "var(--ink-4)" }}
        >
          +{fixedRefsCount} from fixed
        </span>
      </div>
    </div>
  );
}
