// LineageNode — one cell in the lineage graph.
//
// Visual contract (PRD §5.2.1 + reference mock):
//   - 32×32 rounded square, ``var(--paper-2)`` fill, ``var(--ink-2)`` border
//   - current node: banana ring + NOW pill + 1.5px ink inner border
//   - derivation kind chip: small (8×8) marker sitting on the top-right
//     CORNER (half inside / half outside) so it never overlaps the node
//     interior — filled square for mask_edit, hollow for outpaint, none
//     for the lineage root
//   - source-of-next: tiny dot on the parent member that was the
//     direct source for a downstream edit (less obtrusive than the
//     original triangle so it stops feeling like noise)
//   - ghost (parent missing from cache): paper-3 fill, no label inside
//
// Hover preview: when the user dwells on a node we expose a small
// tooltip card with the thumbnail (already shown as bg, but enlarged)
// and the row's summary fields — derivation kind, status, label, when.

import { useEffect, useRef, useState } from "react";

import AuthorizedImage from "../../AuthorizedImage.jsx";

const HOVER_DELAY_MS = 350;

export default function LineageNode({
  nodeKey,
  node,
  label,
  style,
  focused,
  onClick,
  onFocus,
}) {
  const ref = useRef(null);
  const [hover, setHover] = useState(false);
  const hoverTimerRef = useRef(null);

  useEffect(() => {
    if (focused && ref.current) ref.current.focus({ preventScroll: true });
  }, [focused]);

  useEffect(() => () => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
  }, []);

  const isCurrent = !!node.is_current;
  const isGhost = !!node.is_ghost;
  const thumbUrl = node.thumb_url || null;
  const kindMarker = node.derivation_kind || null;

  function onEnter() {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = setTimeout(() => setHover(true), HOVER_DELAY_MS);
  }
  function onLeave() {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHover(false);
  }

  return (
    <div
      ref={ref}
      data-node-key={nodeKey}
      data-current={isCurrent ? "1" : "0"}
      data-derivation-kind={kindMarker || ""}
      data-source-of-next={node.is_source_of_next ? "1" : "0"}
      data-ghost={isGhost ? "1" : "0"}
      data-testid={`me-lineage-node-${node.hash_id}-${node.img_order}`}
      data-seq={node.seq_no ?? ""}
      data-hash={node.hash_id}
      data-order={node.img_order}
      data-depth={node.depth}
      data-lane={node.lane}
      className={`me-lineage-node ${isCurrent ? "is-current" : ""} ${isGhost ? "is-ghost" : ""}`}
      style={style}
      role="treeitem"
      aria-level={node.depth + 1}
      aria-current={isCurrent ? "true" : undefined}
      aria-label={`${label}${kindMarker ? ` ${kindMarker}` : ""}`}
      tabIndex={focused ? 0 : -1}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.();
      }}
      onFocus={onFocus}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
    >
      <div className="me-lineage-node__box">
        <NodeThumb url={thumbUrl} />
        {node.is_source_of_next && (
          <span className="me-lineage-node__source-dot" aria-hidden="true" />
        )}
      </div>
      {/* The kind marker sits ON the corner so it half-overlaps the box
          frame but never reaches into the thumbnail interior. */}
      {kindMarker && (
        <span
          className={`me-lineage-node__kind me-lineage-node__kind--${kindMarker}`}
          aria-hidden="true"
          title={kindMarker === "mask_edit" ? "mask edit" : "outpaint"}
        />
      )}
      <div className="me-lineage-node__label">{label}</div>
      {isCurrent && (
        <span
          className="me-lineage-node__now-pill"
          data-testid="me-lineage-now-pill"
          data-hash-id={node.hash_id}
          data-img-order={node.img_order}
        >
          NOW
        </span>
      )}
      {hover && <NodeHoverCard node={node} label={label} />}
    </div>
  );
}

function NodeThumb({ url }) {
  if (!url) {
    // No thumbnail loaded — leave the box empty (its paper-2 fill is
    // visible). The label below the box already carries the seq number,
    // so a fallback "#39-1" inside the box would just create noise.
    return null;
  }
  return (
    <AuthorizedImage
      src={url}
      className="me-lineage-node__img"
      fallback={null}
    />
  );
}

function NodeHoverCard({ node, label }) {
  const kindWord =
    node.derivation_kind === "mask_edit"
      ? "mask edit"
      : node.derivation_kind === "outpaint"
        ? "outpaint"
        : "original";
  const status = (node.status || "—").toLowerCase();
  return (
    <div
      className="me-lineage-node__hover"
      role="tooltip"
      data-testid="me-lineage-node-hover"
    >
      <div className="me-lineage-node__hover-thumb">
        {node.thumb_url ? (
          <AuthorizedImage
            src={node.thumb_url}
            className="me-lineage-node__hover-img"
            fallback={
              <div className="me-lineage-node__hover-empty">no preview</div>
            }
          />
        ) : (
          <div className="me-lineage-node__hover-empty">no preview</div>
        )}
      </div>
      <div className="me-lineage-node__hover-body">
        <div className="me-lineage-node__hover-title">{label}</div>
        <Row k="kind" v={kindWord} />
        <Row k="status" v={status} />
        {node.model && <Row k="model" v={node.model} />}
        {node.is_create_set_member && (
          <Row k="image" v={`order ${node.img_order} of create-set`} />
        )}
        {node.is_source_of_next && (
          <Row k="role" v="source of a downstream edit" />
        )}
        {node.is_current && <Row k="role" v="current task" />}
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div className="me-lineage-node__hover-row">
      <span className="me-lineage-node__hover-key">{k}</span>
      <span className="me-lineage-node__hover-val">{v}</span>
    </div>
  );
}
