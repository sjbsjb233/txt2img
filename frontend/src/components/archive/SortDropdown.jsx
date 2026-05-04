// Sort dropdown — selection list + a "refresh from server" footer.

import { useEffect, useRef, useState } from "react";

const OPTIONS = [
  { key: "newest", label: "newest first" },
  { key: "oldest", label: "oldest first" },
  { key: "starred-first", label: "starred first" },
  { key: "set-size", label: "largest set" },
];

const EXIT_MS = 140;

export default function SortDropdown({
  current,
  onPick,
  onRefresh,
  onClose,
  triggerRef,
}) {
  const ref = useRef(null);
  const exitTimer = useRef(null);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    return () => {
      if (exitTimer.current) clearTimeout(exitTimer.current);
    };
  }, []);

  // The `closing` state isn't enough as a guard — React batches updates
  // so a rapid double-click could still see `closing === false` on the
  // second call. Use the timer ref as the synchronous lock.
  function deferClose() {
    if (exitTimer.current) return;
    setClosing(true);
    exitTimer.current = setTimeout(() => {
      exitTimer.current = null;
      onClose();
    }, EXIT_MS);
  }
  function deferPick(k) {
    if (exitTimer.current) return;
    setClosing(true);
    exitTimer.current = setTimeout(() => {
      exitTimer.current = null;
      onPick(k);
    }, EXIT_MS);
  }
  function deferRefresh() {
    if (exitTimer.current) return;
    setClosing(true);
    exitTimer.current = setTimeout(() => {
      exitTimer.current = null;
      onRefresh();
    }, EXIT_MS);
  }

  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && ref.current.contains(e.target)) return;
      if (triggerRef?.current && triggerRef.current.contains(e.target)) return;
      deferClose();
    };
    const onKey = (e) => {
      if (e.key === "Escape") deferClose();
    };
    const t = setTimeout(() => document.addEventListener("mousedown", onDoc), 0);
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [closing, triggerRef]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="sort options"
      className={closing ? "arch-pop-close" : "arch-pop-open"}
      style={{
        position: "absolute",
        top: "calc(100% + 6px)",
        right: 0,
        zIndex: 50,
        minWidth: 200,
        background: "var(--card, #fffdf7)",
        border: "1px solid var(--ink)",
        boxShadow: "5px 5px 0 var(--ink)",
        transformOrigin: "top right",
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
            onClick={() => deferPick(opt.key)}
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
        onClick={deferRefresh}
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
