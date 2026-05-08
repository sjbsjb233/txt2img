import { useState } from "react";
import { SectionHeader } from "./atoms.jsx";
import MEIcon from "../MEIcon.jsx";

const OPS = [
  { id: "all", icon: "select-all", label: "all" },
  { id: "none", icon: "deselect", label: "clear" },
  { id: "invert", icon: "invert", label: "invert" },
  { id: "smooth", icon: "smooth", label: "smooth" },
  { id: "feather", icon: "feather", label: "feather" },
  { id: "expand", icon: "expand", label: "expand" },
  { id: "contract", icon: "contract", label: "contract" },
  { id: "trash", icon: "trash", label: "delete" },
];

export default function MaskOpsRow({ onOp, lastOp }) {
  const [params, setParams] = useState({ feather: 6, expand: 5, contract: 5, smooth: 3 });
  const showInput = ["feather", "expand", "contract", "smooth"].includes(lastOp);
  return (
    <div className="me-mask-ops" data-testid="me-mask-ops">
      <SectionHeader>mask operations</SectionHeader>
      <div className="me-mask-ops__grid">
        {OPS.map((b) => (
          <button
            key={b.id}
            className="me-mask-ops__btn"
            onClick={() => onOp?.(b.id, params[b.id])}
            title={b.label}
            data-testid={`me-op-${b.id}`}
          >
            <MEIcon name={b.icon} size={14} />
            <span>{b.label}</span>
          </button>
        ))}
      </div>
      {lastOp && (
        <div className="me-mask-ops__last">
          <span className="me-mask-ops__last-label">last op</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{lastOp}</span>
          {showInput && (
            <>
              <span className="me-mask-ops__last-label" style={{ flex: "0 0 auto" }}>r=</span>
              <input
                className="me-mask-ops__last-input"
                value={`${params[lastOp] ?? 6} px`}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  if (!Number.isFinite(n)) return;
                  setParams({ ...params, [lastOp]: n });
                }}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
