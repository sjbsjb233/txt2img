// Sort dropdown — selection list + a "refresh from server" footer.

import { useEffect, useRef } from "react";

const OPTIONS = [
  { key: "newest", label: "newest first" },
  { key: "oldest", label: "oldest first" },
  { key: "starred-first", label: "starred first" },
  { key: "set-size", label: "largest set" },
];

export default function SortDropdown({
  current,
  onPick,
  onRefresh,
  onClose,
  triggerRef,
}) {
  const ref = useRef(null);

  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && ref.current.contains(e.target)) return;
      if (triggerRef?.current && triggerRef.current.contains(e.target)) return;
      onClose();
    };
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    const t = setTimeout(() => document.addEventListener("mousedown", onDoc), 0);
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose, triggerRef]);

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="sort options"
      style={{
        position: "absolute",
        top: "calc(100% + 6px)",
        right: 0,
        zIndex: 50,
        minWidth: 200,
        background: "var(--card, #fffdf7)",
        border: "1px solid var(--ink)",
        boxShadow: "5px 5px 0 var(--ink)",
        animation: "popIn 140ms cubic-bezier(.2,.9,.3,1)",
      }}
    >
      {OPTIONS.map((opt) => {
        const sel = opt.key === current;
        return (
          <button
            key={opt.key}
            data-testid={`sort-opt-${opt.key}`}
            role="menuitem"
            data-selected={sel ? "true" : "false"}
            onClick={() => onPick(opt.key)}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--paper-2)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
            }}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
              width: "100%",
              padding: "8px 12px",
              border: "none",
              borderBottom: "1px solid var(--rule)",
              background: "transparent",
              cursor: "pointer",
              textAlign: "left",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: sel ? 700 : 500,
              color: "var(--ink)",
            }}
          >
            <span>{opt.label}</span>
            {sel && <span aria-hidden="true">★</span>}
          </button>
        );
      })}
      <button
        data-testid="sort-opt-refresh"
        role="menuitem"
        onClick={onRefresh}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--paper-2)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          width: "100%",
          padding: "8px 12px",
          border: "none",
          borderTop: "2px solid var(--ink)",
          background: "transparent",
          cursor: "pointer",
          textAlign: "left",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          fontWeight: 600,
          color: "var(--ink-2)",
        }}
      >
        ↻ refresh from server
      </button>
    </div>
  );
}
