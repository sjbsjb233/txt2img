// Reusable atomic controls for the right panel (Field, Slider, Toggle, Seg).

export function FieldLabel({ children, extra }) {
  return (
    <div className="me-field-label">
      <span className="me-field-label__name">{children}</span>
      {extra && <span className="me-field-label__extra">{extra}</span>}
    </div>
  );
}

export function Field({ label, extra, children }) {
  return (
    <div className="me-field">
      <FieldLabel extra={extra}>{label}</FieldLabel>
      {children}
    </div>
  );
}

export function Slider({ value, min = 0, max = 100, onChange }) {
  const pct = ((value - min) / (max - min)) * 100;
  function handlePointer(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, x / rect.width));
    onChange?.(min + ratio * (max - min));
  }
  return (
    <div
      className="me-slider"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        handlePointer(e);
      }}
      onPointerMove={(e) => {
        if (e.buttons === 0) return;
        handlePointer(e);
      }}
    >
      <div className="me-slider__track" />
      <div className="me-slider__fill" style={{ width: `${pct}%` }} />
      {[0, 25, 50, 75, 100].map((p) => (
        <div key={p} className="me-slider__tick" style={{ left: `${p}%` }} />
      ))}
      <div className="me-slider__handle" style={{ left: `${pct}%` }} />
    </div>
  );
}

export function Toggle({ on, label, onChange }) {
  return (
    <div className="me-toggle" onClick={() => onChange?.(!on)}>
      <span className="me-toggle__label">{label}</span>
      <div className={`me-toggle__box me-toggle__box--${on ? "on" : "off"}`}>
        <div className="me-toggle__dot" />
      </div>
    </div>
  );
}

export function Seg({ options, value, onChange, dense }) {
  return (
    <div className="me-seg">
      {options.map((o) => {
        const v = typeof o === "string" ? o : o.value;
        const lbl = typeof o === "string" ? o : o.label;
        const on = v === value;
        return (
          <button
            key={v}
            className={`me-seg__btn ${on ? "me-seg__btn--active" : ""} ${dense ? "me-seg__btn--dense" : ""}`}
            onClick={() => onChange?.(v)}
            type="button"
          >
            {lbl}
          </button>
        );
      })}
    </div>
  );
}

export function SectionHeader({ children, action }) {
  return (
    <div className="me-section-header">
      <span className="me-section-header__title">{children}</span>
      {action && <span className="me-section-header__action">{action}</span>}
    </div>
  );
}
