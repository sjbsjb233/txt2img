// Two-column filter popover. Edits a local `pending` snapshot; only
// `apply` commits back to the parent.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  PROPERTY_DEFS,
  computeFilterOptions,
  propertyHasValue,
} from "./archiveFilter.js";
import { EMPTY_FILTER as EMPTY } from "../../store/archivePrefs.js";

const PERIOD_CHOICES = [
  { value: null, label: "any time" },
  { value: "7d", label: "last 7 days" },
  { value: "30d", label: "last 30 days" },
  { value: "90d", label: "last 90 days" },
];

const STATUS_CHOICES = [
  { value: "SUCCEEDED", label: "succeeded" },
  { value: "FAILED", label: "failed" },
  { value: "RUNNING", label: "running" },
  { value: "QUEUED", label: "queued" },
  { value: "CANCELLED", label: "cancelled" },
];

function clone(v) {
  return JSON.parse(JSON.stringify(v));
}

export default function FilterPopover({
  applied,
  allRows,
  sessionLockedByUrl,
  onApply,
  onClose,
}) {
  const ref = useRef(null);
  const [pending, setPending] = useState(() => clone(applied || EMPTY));
  const [activeProp, setActiveProp] = useState("period");

  // Reset pending whenever the popover is reopened (parent toggles by
  // mounting/unmounting, so this just runs on first mount).
  useEffect(() => {
    setPending(clone(applied || EMPTY));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Click-outside + Esc + Enter handlers.
  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        onApply(pending);
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const idx = PROPERTY_DEFS.findIndex((p) => p.id === activeProp);
        if (idx === -1) return;
        const delta = e.key === "ArrowDown" ? 1 : -1;
        const next = (idx + delta + PROPERTY_DEFS.length) % PROPERTY_DEFS.length;
        setActiveProp(PROPERTY_DEFS[next].id);
      }
    };
    const t = setTimeout(() => document.addEventListener("mousedown", onDoc), 0);
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose, onApply, pending, activeProp]);

  const dynamic = useMemo(() => computeFilterOptions(allRows), [allRows]);

  function togglePeriod(value) {
    setPending((p) => ({ ...p, period: value }));
  }
  function toggleMulti(propId, value) {
    setPending((p) => {
      const cur = new Set(p[propId] || []);
      if (cur.has(value)) cur.delete(value);
      else cur.add(value);
      return { ...p, [propId]: [...cur] };
    });
  }
  function toggleStarred() {
    setPending((p) => ({ ...p, starred: !p.starred }));
  }
  function pickSession(value) {
    setPending((p) => ({ ...p, session: value }));
  }
  function clearAll() {
    setPending(clone(EMPTY));
  }

  return (
    <div
      ref={ref}
      data-testid="archive-filter-popover"
      role="dialog"
      aria-label="filter properties"
      style={{
        position: "absolute",
        top: "calc(100% + 8px)",
        left: 0,
        zIndex: 50,
        width: "min(560px, 90vw)",
        background: "var(--card, #fffdf7)",
        border: "1px solid var(--ink)",
        boxShadow: "5px 5px 0 var(--ink)",
        animation: "popIn 140ms cubic-bezier(.2,.9,.3,1)",
        display: "flex",
        flexDirection: "column",
        maxHeight: "min(70vh, 600px)",
      }}
    >
      <style>{`
        @keyframes popIn { from { opacity: 0; transform: translateY(-4px) scale(.98); } to { opacity: 1; transform: none; } }
      `}</style>

      <div style={{ display: "flex", flex: 1, minHeight: 320 }}>
        <div
          style={{
            width: 200,
            borderRight: "1px solid var(--ink)",
            padding: "10px 0",
            background: "var(--paper-2)",
            overflowY: "auto",
          }}
        >
          {PROPERTY_DEFS.map((p) => {
            const active = p.id === activeProp;
            const has = propertyHasValue(pending, p.id);
            return (
              <button
                key={p.id}
                data-testid={`filter-prop-${p.id}`}
                data-active={active ? "true" : "false"}
                onClick={() => setActiveProp(p.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  width: "100%",
                  padding: "10px 14px",
                  background: active ? "var(--ink)" : "transparent",
                  color: active ? "var(--paper)" : "var(--ink)",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                  fontFamily: "var(--font-display)",
                  fontSize: 15,
                  fontWeight: 600,
                }}
              >
                <span>{p.label}</span>
                {has && (
                  <span
                    aria-hidden="true"
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      background: "var(--banana)",
                      border: "1px solid var(--ink)",
                    }}
                  />
                )}
              </button>
            );
          })}
        </div>

        <div
          style={{
            flex: 1,
            minWidth: 0,
            padding: "16px 18px",
            overflowY: "auto",
          }}
        >
          {activeProp === "period" && (
            <PeriodPanel value={pending.period} onChange={togglePeriod} />
          )}
          {activeProp === "model" && (
            <MultiPanel
              propId="model"
              options={dynamic.model.map((v) => ({ value: v, label: v }))}
              selected={pending.model}
              onToggle={(v) => toggleMulti("model", v)}
              emptyHint="No models in your archive yet."
            />
          )}
          {activeProp === "shape" && (
            <MultiPanel
              propId="shape"
              options={dynamic.shape.map((v) => ({ value: v, label: v }))}
              selected={pending.shape}
              onToggle={(v) => toggleMulti("shape", v)}
              emptyHint="No shapes in your archive yet."
            />
          )}
          {activeProp === "status" && (
            <MultiPanel
              propId="status"
              options={STATUS_CHOICES}
              selected={pending.status}
              onToggle={(v) => toggleMulti("status", v)}
            />
          )}
          {activeProp === "starred" && (
            <TogglePanel
              propId="starred"
              value={pending.starred}
              label="show only items with at least one starred image"
              onChange={toggleStarred}
            />
          )}
          {activeProp === "session" && (
            <SessionPanel
              options={dynamic.session}
              value={pending.session}
              onChange={pickSession}
              lockedByUrl={sessionLockedByUrl}
            />
          )}
        </div>
      </div>

      <div
        style={{
          background: "var(--paper-2)",
          borderTop: "1px solid var(--ink)",
          padding: "10px 12px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <button
          data-testid="filter-clear"
          onClick={clearAll}
          style={{
            background: "transparent",
            border: "1px solid var(--ink)",
            padding: "6px 12px",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            cursor: "pointer",
            color: "var(--ink)",
          }}
        >
          clear all
        </button>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            data-testid="filter-cancel"
            onClick={onClose}
            style={{
              background: "transparent",
              border: "1px solid var(--ink-3)",
              padding: "6px 12px",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              cursor: "pointer",
              color: "var(--ink-2)",
            }}
          >
            cancel
          </button>
          <button
            data-testid="filter-apply"
            onClick={() => onApply(pending)}
            style={{
              background: "var(--ink)",
              color: "var(--paper)",
              border: "1px solid var(--ink)",
              padding: "6px 14px",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            apply
          </button>
        </div>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------
// Per-property panels
// -----------------------------------------------------------------

function PeriodPanel({ value, onChange }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <PanelTitle>Period</PanelTitle>
      {PERIOD_CHOICES.map((c) => {
        const selected = c.value === value;
        const isDefault = c.value === null;
        return (
          <button
            key={String(c.value)}
            data-testid={`filter-opt-period-${c.value === null ? "__null" : c.value}`}
            data-selected={selected ? "true" : "false"}
            onClick={() => onChange(c.value)}
            style={{
              padding: "8px 12px",
              border: `1px ${isDefault ? "dashed" : "solid"} var(--ink)`,
              background: selected ? "var(--banana)" : "transparent",
              cursor: "pointer",
              textAlign: "left",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--ink)",
            }}
          >
            {c.label}
          </button>
        );
      })}
    </div>
  );
}

function MultiPanel({ propId, options, selected, onToggle, emptyHint }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <PanelTitle>{propId}</PanelTitle>
      {options.length === 0 && emptyHint ? (
        <div
          style={{
            padding: "8px 4px",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--ink-3)",
            fontStyle: "italic",
          }}
        >
          {emptyHint}
        </div>
      ) : null}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {options.map((opt) => {
          const isSel = (selected || []).includes(opt.value);
          return (
            <button
              key={opt.value}
              data-testid={`filter-opt-${propId}-${opt.value}`}
              data-selected={isSel ? "true" : "false"}
              onClick={() => onToggle(opt.value)}
              style={{
                padding: "6px 12px",
                border: "1px solid var(--ink)",
                borderRadius: 999,
                background: isSel ? "var(--banana)" : "transparent",
                cursor: "pointer",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                fontWeight: 600,
                color: "var(--ink)",
              }}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TogglePanel({ propId, value, label, onChange }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelTitle>{propId}</PanelTitle>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color: "var(--ink-2)",
        }}
      >
        <button
          type="button"
          data-testid={`filter-opt-${propId}-__t`}
          data-selected={value ? "true" : "false"}
          role="switch"
          aria-checked={value}
          aria-label={label}
          onClick={onChange}
          style={{
            width: 38,
            height: 22,
            padding: 0,
            border: "1px solid var(--ink)",
            background: value ? "var(--banana)" : "var(--paper-2)",
            position: "relative",
            display: "inline-flex",
            alignItems: "center",
            cursor: "pointer",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: 1,
              left: value ? 18 : 1,
              width: 18,
              height: 18,
              background: "var(--ink)",
              transition: "left 120ms ease",
            }}
          />
        </button>
        <label
          onClick={onChange}
          style={{ cursor: "pointer" }}
        >
          {label}
        </label>
      </div>
    </div>
  );
}

function SessionPanel({ options, value, onChange, lockedByUrl }) {
  return (
    <div
      data-disabled={lockedByUrl ? "true" : "false"}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        opacity: lockedByUrl ? 0.5 : 1,
        pointerEvents: lockedByUrl ? "none" : "auto",
      }}
    >
      <PanelTitle>Session</PanelTitle>
      {lockedByUrl && (
        <div
          style={{
            padding: "6px 8px",
            border: "1px dashed var(--ink-3)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--ink-3)",
          }}
        >
          session locked by URL — clear via URL
        </div>
      )}
      <button
        data-testid="filter-opt-session-__null"
        data-selected={!value ? "true" : "false"}
        onClick={() => onChange(null)}
        style={{
          padding: "8px 12px",
          border: "1px dashed var(--ink)",
          background: !value ? "var(--banana)" : "transparent",
          cursor: "pointer",
          textAlign: "left",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          fontWeight: 600,
        }}
      >
        any session
      </button>
      {options.map((opt) => {
        const sel = opt.id === value;
        return (
          <button
            key={opt.id}
            data-testid={`filter-opt-session-${opt.id}`}
            data-selected={sel ? "true" : "false"}
            onClick={() => onChange(opt.id)}
            style={{
              padding: "8px 12px",
              border: "1px solid var(--ink)",
              background: sel ? "var(--banana)" : "transparent",
              cursor: "pointer",
              textAlign: "left",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 600,
            }}
          >
            {opt.name}
          </button>
        );
      })}
      {!options.length && (
        <div
          style={{
            padding: "8px 4px",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--ink-3)",
            fontStyle: "italic",
          }}
        >
          No sessions in your archive yet.
        </div>
      )}
    </div>
  );
}

function PanelTitle({ children }) {
  return (
    <div
      className="mono caps"
      style={{
        fontSize: 10,
        color: "var(--ink-3)",
        letterSpacing: "0.18em",
        marginBottom: 4,
      }}
    >
      {children}
    </div>
  );
}
