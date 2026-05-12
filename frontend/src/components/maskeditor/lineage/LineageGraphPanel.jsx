// LineageGraphPanel — git-network view of a job's ancestry & descendants.
//
// Replaces the old mask-paint undo list as the "history" tab in the
// right panel (PRD §5.1). Renders nodes / edges / set frames produced
// by the ``useLineage`` selector and exposes:
//   - click a node to navigate
//   - "go to root" jump
//   - keyboard navigation (↑/↓ depth, ←/→ siblings, Enter open, Esc)
//   - banana ring + NOW pill on the current node, auto-centred
// No backend round-trips — pulls from archiveStore via useLineage().

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useLineage } from "./useLineage.js";
import LineageNode from "./LineageNode.jsx";
import LineageSetGroup from "./LineageSetGroup.jsx";
import PathBreadcrumb from "./PathBreadcrumb.jsx";
import { formatNodeLabel, MAX_LANES } from "./computeLineage.js";

const NODE_W = 56;
const NODE_H = 56;
const ROW_GAP = 56; // vertical between depths
const COL_GAP = 24; // horizontal between lane slots

export default function LineageGraphPanel({ currentHashId, currentOrder = 1 }) {
  const navigate = useNavigate();
  const lineage = useLineage(currentHashId, currentOrder);
  const scrollerRef = useRef(null);
  const [focusKey, setFocusKey] = useState(null);
  const [showJumpBack, setShowJumpBack] = useState(false);

  // Initialise focus on the current node when lineage refreshes.
  useEffect(() => {
    if (!lineage) return;
    const cur = lineage.nodes.find((n) => n.is_current);
    if (cur) setFocusKey(`${cur.hash_id}#${cur.img_order}`);
  }, [lineage?.current_hash_id, lineage?.current_order, lineage?.nodes?.length]);

  // Auto-centre the current node.
  useEffect(() => {
    if (!lineage || !scrollerRef.current) return;
    const cur = lineage.nodes.find((n) => n.is_current);
    if (!cur) return;
    const target = scrollerRef.current.querySelector(
      `[data-node-key="${cur.hash_id}#${cur.img_order}"]`
    );
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [lineage?.current_hash_id, lineage?.current_order]);

  // Manual scroll → show the "back to current" pill + up/down scroll
  // hints when there is offscreen content in either direction.
  const [hintAbove, setHintAbove] = useState(false);
  const [hintBelow, setHintBelow] = useState(false);
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    function refresh() {
      const cur = el.querySelector('[data-current="1"]');
      if (cur) {
        const elBox = el.getBoundingClientRect();
        const cBox = cur.getBoundingClientRect();
        const visible = cBox.top >= elBox.top && cBox.bottom <= elBox.bottom;
        setShowJumpBack(!visible);
      }
      setHintAbove(el.scrollTop > 4);
      setHintBelow(el.scrollHeight - el.scrollTop - el.clientHeight > 4);
    }
    refresh();
    el.addEventListener("scroll", refresh);
    return () => el.removeEventListener("scroll", refresh);
  }, [lineage?.nodes?.length]);

  // Geometry: depth → lane → node coordinates.
  const layout = useMemo(() => {
    if (!lineage) return null;
    const depths = new Set();
    for (const node of lineage.nodes) depths.add(node.depth);
    const depthList = Array.from(depths).sort((a, b) => a - b);
    const depthIndex = new Map(depthList.map((d, i) => [d, i]));
    const totalRows = depthList.length;
    const totalCols = MAX_LANES;
    const width = totalCols * (NODE_W + COL_GAP);
    const height = totalRows * (NODE_H + ROW_GAP) + 16;
    const nodeAt = (hashId, order) => {
      const node = lineage.nodes.find(
        (n) => n.hash_id === hashId && n.img_order === order
      );
      if (!node) return null;
      const rowIdx = depthIndex.get(node.depth) ?? 0;
      const colIdx = node.lane;
      const x = colIdx * (NODE_W + COL_GAP) + COL_GAP / 2;
      const y = rowIdx * (NODE_H + ROW_GAP) + 12;
      return { x, y, node };
    };
    return { width, height, nodeAt, depthList };
  }, [lineage]);

  if (!lineage) {
    return (
      <div className="me-lineage-empty">
        <div className="me-lineage-empty__note">lineage loading…</div>
      </div>
    );
  }

  function navigateTo(hashId, order) {
    if (!hashId) return;
    if (hashId === currentHashId && order === currentOrder) return;
    navigate(`/edit/${hashId}/${order || 1}`);
  }

  function onKeyDown(e) {
    if (!lineage) return;
    const cur = lineage.nodes.find(
      (n) => `${n.hash_id}#${n.img_order}` === focusKey
    ) || lineage.nodes.find((n) => n.is_current);
    if (!cur) return;
    const k = e.key;
    if (k === "Enter") {
      e.preventDefault();
      navigateTo(cur.hash_id, cur.img_order);
      return;
    }
    if (k === "Escape") {
      setFocusKey(null);
      return;
    }
    let next = null;
    if (k === "ArrowUp") {
      next = pickNeighbor(lineage.nodes, cur, -1, "depth");
    } else if (k === "ArrowDown") {
      next = pickNeighbor(lineage.nodes, cur, +1, "depth");
    } else if (k === "ArrowLeft") {
      next = pickNeighbor(lineage.nodes, cur, -1, "lane");
    } else if (k === "ArrowRight") {
      next = pickNeighbor(lineage.nodes, cur, +1, "lane");
    }
    if (next) {
      e.preventDefault();
      setFocusKey(`${next.hash_id}#${next.img_order}`);
    }
  }

  const totalNodes = lineage.nodes.length;
  const goToRoot = () => {
    navigate(`/edit/${lineage.root_hash_id}/${lineage.root_order || 1}`);
  };

  return (
    <div
      className="me-lineage"
      data-testid="me-lineage-graph"
      data-busy="false"
      data-node-count={lineage.nodes.length}
      data-current-hash={lineage.current_hash_id}
      data-current-order={lineage.current_order}
      onKeyDown={onKeyDown}
      tabIndex={0}
      role="tree"
      aria-label="job lineage"
    >
      <div className="me-lineage__head">
        <span className="me-lineage__head-title">LINEAGE</span>
        <span className="me-lineage__head-count">
          {totalNodes} {totalNodes === 1 ? "node" : "nodes"}
          {lineage.groups.length > 0
            ? ` · ${lineage.groups.length} ${
                lineage.groups.length === 1 ? "set" : "sets"
              }`
            : ""}
        </span>
      </div>
      <div className="me-lineage__path" data-testid="me-lineage-breadcrumb">
        <PathBreadcrumb
          path={lineage.path}
          onPick={(hashId, order) => navigateTo(hashId, order)}
        />
      </div>
      <div className="me-lineage__rule" />
      <div
        className="me-lineage__scroller"
        ref={scrollerRef}
        data-testid="me-lineage-scroller"
      >
        {lineage.truncated && (
          <div className="me-lineage__hint">↑ more nodes hidden</div>
        )}
        {hintAbove && !lineage.truncated && (
          <div className="me-lineage__hint" data-testid="me-lineage-hint-above">↑ scroll for older</div>
        )}
        {totalNodes === 1 && (
          <div className="me-lineage__hint">↑ root of this lineage</div>
        )}
        <div
          className="me-lineage__canvas"
          style={{ width: layout.width, height: layout.height }}
        >
          {/* Edges (SVG) */}
          <svg
            className="me-lineage__edges"
            width={layout.width}
            height={layout.height}
          >
            {lineage.edges.map((edge, i) => {
              const from = layout.nodeAt(edge.from_hash_id, edge.from_order);
              const to = layout.nodeAt(edge.to_hash_id, edge.to_order);
              if (!from || !to) return null;
              const x1 = from.x + NODE_W / 2;
              const y1 = from.y + NODE_H;
              const x2 = to.x + NODE_W / 2;
              const y2 = to.y;
              const midY = (y1 + y2) / 2;
              const stroke = edge.to_is_current ? "var(--ink-2)" : "var(--ink-3)";
              const width = edge.to_is_current ? 1.5 : 1;
              return (
                <path
                  key={i}
                  d={`M ${x1} ${y1} L ${x1} ${midY} L ${x2} ${midY} L ${x2} ${y2}`}
                  stroke={stroke}
                  strokeWidth={width}
                  fill="none"
                />
              );
            })}
          </svg>
          {/* Set group frames */}
          {lineage.groups.map((g) => {
            const first = layout.nodeAt(g.members[0].hash_id, g.members[0].img_order);
            const last = layout.nodeAt(
              g.members[g.members.length - 1].hash_id,
              g.members[g.members.length - 1].img_order
            );
            if (!first || !last) return null;
            const left = Math.min(first.x, last.x) - 6;
            const right = Math.max(first.x, last.x) + NODE_W + 6;
            const top = first.y - 14;
            const height = NODE_H + 18;
            return (
              <LineageSetGroup
                key={`${g.set_id}-${g.depth}`}
                kind={g.kind}
                set_id={g.set_id}
                count={g.members.length}
                style={{
                  left,
                  top,
                  width: right - left,
                  height,
                }}
              />
            );
          })}
          {/* Nodes */}
          {lineage.nodes.map((node) => {
            const pos = layout.nodeAt(node.hash_id, node.img_order);
            if (!pos) return null;
            const key = `${node.hash_id}#${node.img_order}`;
            return (
              <LineageNode
                key={key}
                nodeKey={key}
                node={node}
                style={{
                  position: "absolute",
                  left: pos.x,
                  top: pos.y,
                  width: NODE_W,
                  height: NODE_H,
                }}
                label={formatNodeLabel(node)}
                focused={focusKey === key}
                onClick={() => navigateTo(node.hash_id, node.img_order)}
                onFocus={() => setFocusKey(key)}
              />
            );
          })}
        </div>
        {lineage.truncated && (
          <div className="me-lineage__hint">↓ more nodes hidden</div>
        )}
        {hintBelow && !lineage.truncated && (
          <div className="me-lineage__hint" data-testid="me-lineage-hint-below">↓ scroll for newer</div>
        )}
      </div>
      {showJumpBack && (
        <button
          className="me-lineage__jumpback"
          type="button"
          onClick={() => {
            const cur = scrollerRef.current?.querySelector('[data-current="1"]');
            cur?.scrollIntoView({ block: "center", behavior: "smooth" });
          }}
          data-testid="me-lineage-back-to-current"
        >
          back to current
        </button>
      )}
      <div className="me-lineage__rule" />
      <div className="me-lineage__foot">
        <span>click · ⏎ to open</span>
        <button
          type="button"
          className="me-lineage__root-btn"
          onClick={goToRoot}
          disabled={lineage.root_hash_id === currentHashId}
          data-testid="me-lineage-go-root"
        >
          go to root ↑
        </button>
      </div>
    </div>
  );
}

function pickNeighbor(nodes, current, dir, axis) {
  if (axis === "depth") {
    const candidates = nodes.filter((n) => n.lane === current.lane);
    candidates.sort((a, b) => a.depth - b.depth);
    const idx = candidates.findIndex(
      (n) => n.hash_id === current.hash_id && n.img_order === current.img_order
    );
    if (idx === -1) return null;
    const target = candidates[idx + dir];
    return target || null;
  }
  // lane axis: same depth.
  const peers = nodes
    .filter((n) => n.depth === current.depth)
    .sort((a, b) => a.lane - b.lane);
  const idx = peers.findIndex(
    (n) => n.hash_id === current.hash_id && n.img_order === current.img_order
  );
  if (idx === -1) return null;
  return peers[idx + dir] || null;
}
