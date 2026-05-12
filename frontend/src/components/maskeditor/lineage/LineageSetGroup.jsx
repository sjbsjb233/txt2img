// Dashed-frame around a create-set or batch-set in the lineage graph.

export default function LineageSetGroup({ kind, set_id, count, style }) {
  const label = kind === "create"
    ? `create-set · ${shortId(set_id)} · ${count} ${count === 1 ? "image" : "images"}`
    : `batch-set · ${shortId(set_id)} · ${count} ${count === 1 ? "job" : "jobs"}`;
  return (
    <div
      className={`me-lineage-set me-lineage-set--${kind}`}
      style={style}
      role="group"
      aria-label={label}
      data-set-id={set_id}
      data-set-kind={kind}
      data-testid={`me-lineage-set-group-${set_id}`}
    >
      <span className="me-lineage-set__label">{label}</span>
    </div>
  );
}

function shortId(id) {
  if (!id) return "—";
  if (id.startsWith("set_")) return id.slice(4, 10);
  return id.slice(0, 6);
}
