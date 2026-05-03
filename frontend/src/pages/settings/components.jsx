// Shared atoms for the Settings page. Kept thin so each section file
// can stay declarative and the visual language stays consistent.

import Icon from "../../components/Icon.jsx";

// ---------------------------------------------------------------------------
// Toggle switch
// ---------------------------------------------------------------------------

export function Toggle({ checked, onChange, disabled, "aria-label": ariaLabel }) {
  return (
    <button
      type="button"
      className="toggle"
      data-on={checked ? "true" : "false"}
      role="switch"
      aria-checked={checked ? "true" : "false"}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={(e) => {
        e.preventDefault();
        if (disabled) return;
        onChange(!checked);
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Segmented control — exclusive choice from a small list of options.
// ---------------------------------------------------------------------------

export function Segmented({ value, options, onChange, disabled }) {
  return (
    <div
      className="segmented"
      role="radiogroup"
      style={disabled ? { opacity: 0.55 } : undefined}
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="radio"
          aria-checked={opt.value === value}
          data-on={opt.value === value ? "true" : "false"}
          disabled={disabled}
          onClick={() => onChange(opt.value)}
          title={opt.title}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One labelled row in a section card. ``hint`` is the secondary line of
// fine-print copy under the label; ``help`` shows the "?" tooltip icon.
// ---------------------------------------------------------------------------

export function SettingRow({ label, hint, help, children, danger }) {
  return (
    <div className="set-row">
      <div className="set-row-body">
        <span className="label" style={danger ? { color: "var(--bad)" } : undefined}>
          {label}
          {help && <HelpTooltip text={help} />}
        </span>
        {hint && <span className="hint">{hint}</span>}
      </div>
      <div className="set-row-control">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tooltip "?" icon. Tooltip body lives in data-tooltip and is positioned
// via CSS in tokens.css — no JS popper needed for short copy.
// ---------------------------------------------------------------------------

export function HelpTooltip({ text }) {
  return (
    <button
      type="button"
      tabIndex={0}
      className="help-icon"
      data-tooltip={text}
      aria-label="More info"
      onClick={(e) => e.preventDefault()}
    >
      ?
    </button>
  );
}

// ---------------------------------------------------------------------------
// Section card head: serif title + small mono number + optional right chip.
// ---------------------------------------------------------------------------

export function SectionHead({ number, title, right }) {
  return (
    <div className="set-section-head">
      <div className="head-left">
        <span className="num">{number}</span>
        <span className="title">{title}</span>
      </div>
      {right && <div>{right}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card wrapper that adds the brutalist offset shadow to a section.
// ---------------------------------------------------------------------------

export function SectionCard({ children, danger }) {
  return (
    <div
      className="card-brutal"
      style={
        danger
          ? { borderColor: "var(--bad)" }
          : undefined
      }
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny utility: a small mono "Right now" / "Beta" / "Coming soon" chip.
// ---------------------------------------------------------------------------

export function Chip({ children, tone = "default" }) {
  const cls = ["chip"];
  if (tone === "ok") cls.push("ok");
  else if (tone === "warn") cls.push("warn");
  else if (tone === "bad") cls.push("bad");
  else if (tone === "banana") cls.push("banana");
  else if (tone === "solid") cls.push("solid");
  return <span className={cls.join(" ")}>{children}</span>;
}

// Re-export the shared icon for convenience inside section files.
export { Icon };
