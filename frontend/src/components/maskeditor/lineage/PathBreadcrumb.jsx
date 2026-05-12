// Inline breadcrumb showing the direct ancestor chain root → current.
// Long chains (>6 hops) collapse the middle behind a clickable ellipsis.

import { useState } from "react";

import { formatNodeLabel } from "./computeLineage.js";

const MAX_INLINE = 6;

export default function PathBreadcrumb({ path, onPick }) {
  const [expanded, setExpanded] = useState(false);

  if (!path || path.length === 0) {
    return <span className="me-lineage-path-empty">no path</span>;
  }

  let visible = path;
  if (!expanded && path.length > MAX_INLINE) {
    // Always keep the first 2 + last 3 around the ellipsis.
    visible = [path[0], path[1], "...", ...path.slice(path.length - 3)];
  }

  return (
    <div className="me-lineage-path" data-testid="me-lineage-path">
      {visible.map((step, i) => {
        if (step === "...") {
          return (
            <button
              key={`ell-${i}`}
              type="button"
              className="me-lineage-path__ellipsis"
              onClick={() => setExpanded(true)}
              data-testid="me-lineage-path-expand"
              aria-label="expand the full lineage path"
            >
              …
            </button>
          );
        }
        const label = formatNodeLabel(step);
        const isLast = i === visible.length - 1;
        return (
          <span key={`${step.hash_id}#${step.img_order}`} className="me-lineage-path__step">
            <button
              type="button"
              className={`me-lineage-path__btn ${step.is_current ? "is-current" : ""}`}
              onClick={() => !step.is_current && onPick?.(step.hash_id, step.img_order)}
              disabled={step.is_current}
              data-current={step.is_current ? "1" : "0"}
            >
              {label}
            </button>
            {!isLast && <span className="me-lineage-path__sep">›</span>}
          </span>
        );
      })}
    </div>
  );
}
