// Pure function: derive a lineage graph for a single ``hash_id`` from
// the in-memory archive rows. Returns nodes / edges / set groups ready
// to render — no backend round-trips. See PRD §5.6 for the data flow.
//
// The selector intentionally tolerates incomplete data:
//   - A parent referenced by ``parent_hash_id`` but not present in the
//     row map is surfaced as a "ghost" node (label "#?") so the graph
//     can still render; the calling hook fires ``archiveStore.refreshDetail``
//     in the background to fill it in.
//   - Reachable nodes are capped at ``MAX_NODES`` to keep render cost
//     bounded. ``truncated: true`` signals the panel to show a banner.

export const MAX_NODES = 200;
export const MAX_LANES = 24;
export const MAX_CREATE_SET_SIZE_FOR_EXPANSION = 8;

const _SETID_BATCH_MAP = new WeakMap();

function shortHash(hashId) {
  if (!hashId) return "—";
  if (hashId.startsWith("j_")) return hashId.slice(2, 8);
  return hashId.slice(0, 6);
}

function classifySetKind(rowsInSet) {
  // create-set ⇔ exactly one jobs row sharing the set_id (every image
  // there is a different ``img_order`` on the same hash_id).
  // batch-set ⇔ multiple jobs rows sharing the set_id (each its own
  // hash_id + seq_no).
  if (!rowsInSet || rowsInSet.length === 0) return null;
  return rowsInSet.length === 1 ? "create" : "batch";
}

/**
 * Build a snapshot of the ``rows`` map keyed by ``set_id`` so we can
 * classify create-set vs batch-set in O(1) per node.
 */
function indexBySetId(rows) {
  const bySet = new Map();
  for (const row of rows.values()) {
    if (!row?.set_id) continue;
    if (!bySet.has(row.set_id)) bySet.set(row.set_id, []);
    bySet.get(row.set_id).push(row);
  }
  return bySet;
}

/**
 * Expand a row into one or more ``LineageMember`` records. Create-set
 * rows fan out into N members (one per image order); everything else
 * yields a single member with ``img_order=1``.
 */
function expandRowIntoMembers(row, setKind) {
  if (setKind === "create") {
    const images = Array.isArray(row.images) ? row.images : [];
    // If we don't have image metadata yet, fall back to a single
    // member; the SSE / detail sync will fill in the order list.
    const orders = images.length > 0
      ? images.map((img) => img.order).filter((o) => Number.isFinite(o))
      : [1];
    return orders
      .slice()
      .sort((a, b) => a - b)
      .map((o) => ({
        hash_id: row.hash_id,
        img_order: o,
        is_create_set_member: true,
      }));
  }
  return [{
    hash_id: row.hash_id,
    img_order: 1,
    is_create_set_member: false,
  }];
}

function makeMemberKey(hashId, order) {
  return `${hashId}#${order}`;
}

/**
 * Walk parents upward to find lineage root.
 *
 * "Root" is the first ancestor with no ``parent_hash_id`` (or whose
 * parent is missing from the row map). When the row itself is missing
 * we treat ``hashId`` as its own root — the caller still gets a
 * single ghost node.
 */
function findRoot(rows, hashId) {
  let cursor = hashId;
  const visited = new Set();
  while (cursor) {
    if (visited.has(cursor)) break;
    visited.add(cursor);
    const row = rows.get(cursor);
    if (!row || !row.parent_hash_id) return cursor;
    cursor = row.parent_hash_id;
  }
  return cursor || hashId;
}

/**
 * Build the lineage graph for ``currentHashId``.
 *
 * Returns ``null`` when ``rows`` is empty (e.g. archiveStore hasn't
 * hydrated yet) — the panel renders a "loading" placeholder.
 */
export function computeLineage(rows, currentHashId, options = {}) {
  if (!rows || !currentHashId) return null;
  const currentOrder = Math.max(1, Number(options.currentOrder) || 1);

  const setIndex = indexBySetId(rows);
  const ghostNodes = new Set();

  // Pre-index children by ``parent_hash_id`` so the BFS below is O(N)
  // total instead of O(N*M) (scanning all rows for every queued node).
  // Important when ``rows`` grows large — the selector reruns on every
  // archiveStore notify.
  const childrenByParent = new Map();
  for (const row of rows.values()) {
    const p = row?.parent_hash_id;
    if (!p) continue;
    if (!childrenByParent.has(p)) childrenByParent.set(p, []);
    childrenByParent.get(p).push(row.hash_id);
  }

  const rootHashId = findRoot(rows, currentHashId);

  // BFS from root to collect reachable hashIds. Hard cap at MAX_NODES —
  // once we'd cross the cap we stop enqueuing and mark truncated.
  const reachable = new Set([rootHashId]);
  const queue = [rootHashId];
  let truncated = false;
  while (queue.length > 0) {
    const cur = queue.shift();
    const childHashes = childrenByParent.get(cur) || [];
    for (const childHash of childHashes) {
      if (reachable.has(childHash)) continue;
      if (reachable.size >= MAX_NODES) {
        truncated = true;
        break;
      }
      reachable.add(childHash);
      queue.push(childHash);
    }
    if (truncated) break;
  }

  // Expand each reachable row into virtual members.
  const memberByKey = new Map();
  const orderByHash = new Map(); // hash_id → array of img_order on that hash

  for (const hashId of reachable) {
    const row = rows.get(hashId);
    if (!row) {
      ghostNodes.add(hashId);
      const key = makeMemberKey(hashId, 1);
      memberByKey.set(key, {
        hash_id: hashId,
        seq_no: null,
        set_id: null,
        set_kind: null,
        img_order: 1,
        is_create_set_member: false,
        parent_hash_id: null,
        parent_order: null,
        derivation_kind: null,
        thumb_url: null,
        status: null,
        model: null,
        is_ghost: true,
        depth: 0,
        lane: 0,
        is_current: false,
        is_source_of_next: false,
      });
      orderByHash.set(hashId, [1]);
      continue;
    }
    const setRows = row.set_id ? setIndex.get(row.set_id) : null;
    const setKind = setRows ? classifySetKind(setRows) : null;
    const members = expandRowIntoMembers(row, setKind);
    orderByHash.set(hashId, members.map((m) => m.img_order));
    for (const m of members) {
      const key = makeMemberKey(m.hash_id, m.img_order);
      const thumb = pickThumbUrl(row, m.img_order);
      memberByKey.set(key, {
        hash_id: row.hash_id,
        seq_no: typeof row.seq_no === "number" ? row.seq_no : null,
        set_id: row.set_id || null,
        set_kind: setKind,
        img_order: m.img_order,
        is_create_set_member: m.is_create_set_member,
        parent_hash_id: row.parent_hash_id || null,
        parent_order: row.parent_order ?? null,
        derivation_kind: row.derivation_kind || null,
        thumb_url: thumb,
        status: row.status || null,
        model: row.model || null,
        is_ghost: false,
        depth: 0,
        lane: 0,
        is_current: false,
        is_source_of_next: false,
      });
    }
  }

  // Build adjacency: parentKey → [childKey,...]
  const childrenByParentKey = new Map();
  for (const member of memberByKey.values()) {
    if (!member.parent_hash_id) continue;
    const order = member.parent_order ?? 1;
    const parentKey = makeMemberKey(member.parent_hash_id, order);
    // If the addressed parent member doesn't exist (e.g. parent_order is
    // out of bounds, or parent isn't in rows), fall back to order=1 of
    // the parent so the edge still lands somewhere visible.
    let effectiveKey = parentKey;
    if (!memberByKey.has(parentKey)) {
      const fallback = makeMemberKey(member.parent_hash_id, 1);
      if (memberByKey.has(fallback)) effectiveKey = fallback;
      else {
        // Surface a ghost parent so the lineage still draws.
        memberByKey.set(fallback, {
          hash_id: member.parent_hash_id,
          seq_no: null,
          set_id: null,
          set_kind: null,
          img_order: 1,
          is_create_set_member: false,
          parent_hash_id: null,
          parent_order: null,
          derivation_kind: null,
          thumb_url: null,
          status: null,
          model: null,
          is_ghost: true,
          depth: 0,
          lane: 0,
          is_current: false,
          is_source_of_next: false,
        });
        ghostNodes.add(member.parent_hash_id);
        effectiveKey = fallback;
      }
    }
    if (!childrenByParentKey.has(effectiveKey)) {
      childrenByParentKey.set(effectiveKey, []);
    }
    childrenByParentKey.get(effectiveKey).push(member);
    // Mark the parent member as source-of-next; if multiple children
    // exist they all set the same flag — idempotent.
    const parentMember = memberByKey.get(effectiveKey);
    parentMember.is_source_of_next = true;
  }

  // BFS from rootMember(s) to compute depth.
  // The lineage root might itself be a create-set: depth=0 covers all
  // its members. Otherwise depth=0 is just its single member.
  const rootRow = rows.get(rootHashId);
  const rootOrders = orderByHash.get(rootHashId) || [1];
  const rootMembers = rootOrders.map((o) => memberByKey.get(makeMemberKey(rootHashId, o))).filter(Boolean);

  // Compute depth via BFS (member graph).
  const depthOfKey = new Map();
  const bfs = [];
  for (const m of rootMembers) {
    const key = makeMemberKey(m.hash_id, m.img_order);
    depthOfKey.set(key, 0);
    bfs.push(key);
  }
  while (bfs.length > 0) {
    const key = bfs.shift();
    const d = depthOfKey.get(key);
    const kids = childrenByParentKey.get(key) || [];
    for (const child of kids) {
      const childKey = makeMemberKey(child.hash_id, child.img_order);
      // For a create-set child: every sibling-member shares the same
      // depth (the whole set lives on one level).
      const childRow = rows.get(child.hash_id);
      if (childRow) {
        const peerOrders = orderByHash.get(child.hash_id) || [child.img_order];
        for (const o of peerOrders) {
          const peerKey = makeMemberKey(child.hash_id, o);
          if (!depthOfKey.has(peerKey)) {
            depthOfKey.set(peerKey, d + 1);
            bfs.push(peerKey);
          }
        }
      } else if (!depthOfKey.has(childKey)) {
        depthOfKey.set(childKey, d + 1);
        bfs.push(childKey);
      }
    }
  }

  // Anything not reached (orphans / disconnected from the root walk):
  // give them a sentinel depth so they still get placed. Should be
  // rare; mostly an extra-defensive guard against cycles.
  for (const key of memberByKey.keys()) {
    if (!depthOfKey.has(key)) {
      depthOfKey.set(key, 0);
    }
  }

  // Group members by depth and by hash (create-set siblings share row).
  const depths = new Map(); // depth -> Map<hash_id, member[]>
  for (const member of memberByKey.values()) {
    const key = makeMemberKey(member.hash_id, member.img_order);
    member.depth = depthOfKey.get(key) || 0;
    if (!depths.has(member.depth)) depths.set(member.depth, new Map());
    const byHash = depths.get(member.depth);
    if (!byHash.has(member.hash_id)) byHash.set(member.hash_id, []);
    byHash.get(member.hash_id).push(member);
  }

  // Assign lanes per depth. Strategy:
  //   - List the rows (groups of members) at this depth, sorted by
  //     ``updated_at ASC, hash_id ASC``.
  //   - Each group occupies a contiguous range of lanes equal to the
  //     number of its members (1 for non-create-set; N for create-set).
  //   - The first group inherits its parent's start lane; subsequent
  //     groups take the next free lane slot.
  //   - Cap the total lane usage at MAX_LANES — overflow groups get
  //     ``hidden=true`` and the panel renders a "+N more" pill.
  const groupsAtDepth = new Map();
  for (const [depth, byHash] of depths.entries()) {
    const groups = [];
    for (const [hash, members] of byHash.entries()) {
      members.sort((a, b) => a.img_order - b.img_order);
      const row = rows.get(hash);
      groups.push({
        hash,
        members,
        set_id: members[0].set_id,
        set_kind: members[0].set_kind,
        updated_at: row?.updated_at || "",
      });
    }
    groups.sort((a, b) => {
      const ua = a.updated_at || "";
      const ub = b.updated_at || "";
      if (ua !== ub) return ua.localeCompare(ub);
      return a.hash.localeCompare(b.hash);
    });
    groupsAtDepth.set(depth, groups);
  }

  // Assign lanes top-down so each group gets a lane "near" its parent.
  const sortedDepths = Array.from(groupsAtDepth.keys()).sort((a, b) => a - b);
  const laneStartByKey = new Map(); // memberKey → start lane

  for (const depth of sortedDepths) {
    const groups = groupsAtDepth.get(depth);
    const used = new Array(MAX_LANES).fill(false);
    // For depth 0, position groups starting at lane 0.
    // For deeper depths, prefer the parent's lane.
    const hint = new Map(); // groupIndex -> preferred startLane
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      if (depth === 0) {
        hint.set(i, 0);
        continue;
      }
      // Find any parent member key for this group.
      const someMember = g.members[0];
      const parentOrder = someMember.parent_order ?? 1;
      const parentKey = someMember.parent_hash_id
        ? makeMemberKey(someMember.parent_hash_id, parentOrder)
        : null;
      const fallbackKey = someMember.parent_hash_id
        ? makeMemberKey(someMember.parent_hash_id, 1)
        : null;
      const parentStart = (parentKey && laneStartByKey.get(parentKey))
        ?? (fallbackKey && laneStartByKey.get(fallbackKey))
        ?? 0;
      hint.set(i, parentStart);
    }
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      const span = Math.min(g.members.length, MAX_LANES);
      const preferred = hint.get(i) || 0;
      let start = findFreeRange(used, preferred, span);
      if (start === -1) {
        // No free contiguous range — instead of dropping the group
        // (which silently hides nodes), pin it to the rightmost lane
        // so it still renders in the now-pannable canvas. Members are
        // tagged so the panel can apply a subtle overflow style later.
        start = Math.max(0, MAX_LANES - span);
        for (const m of g.members) m.lane_overflowed = true;
      }
      for (let k = 0; k < span; k++) used[start + k] = true;
      for (let k = 0; k < g.members.length; k++) {
        const m = g.members[k];
        const lane = k < span ? start + k : start + span - 1;
        m.lane = lane;
        const mKey = makeMemberKey(m.hash_id, m.img_order);
        laneStartByKey.set(mKey, start);
      }
    }
  }

  // Mark the current node.
  const currentKey = makeMemberKey(currentHashId, currentOrder);
  if (memberByKey.has(currentKey)) {
    memberByKey.get(currentKey).is_current = true;
  } else {
    // Fall back to order=1 if the requested order is missing.
    const fallback = makeMemberKey(currentHashId, 1);
    if (memberByKey.has(fallback)) memberByKey.get(fallback).is_current = true;
  }

  // Build edges from member → member (skip hidden_overflow).
  const edges = [];
  for (const member of memberByKey.values()) {
    if (!member.parent_hash_id) continue;
    if (member.hidden_overflow) continue;
    const order = member.parent_order ?? 1;
    let parentKey = makeMemberKey(member.parent_hash_id, order);
    if (!memberByKey.has(parentKey)) {
      parentKey = makeMemberKey(member.parent_hash_id, 1);
    }
    const parent = memberByKey.get(parentKey);
    if (!parent) continue;
    if (parent.hidden_overflow) continue;
    edges.push({
      from_hash_id: parent.hash_id,
      from_order: parent.img_order,
      to_hash_id: member.hash_id,
      to_order: member.img_order,
      to_is_current: member.is_current,
    });
  }

  // Build set-frame descriptors for visual framing.
  //
  // Create-set: one hash, many image orders → one per-hash group with
  // N members; emit the frame as-is.
  // Batch-set: multiple hashes share one set_id but each carries one
  // image order; coalesce them by set_id (within a depth) so the dashed
  // frame spans all members.
  const groups = [];
  for (const depth of sortedDepths) {
    const bySet = new Map(); // set_id -> { kind, members[] }
    for (const g of groupsAtDepth.get(depth)) {
      if (!g.set_id || !g.set_kind) continue;
      if (!bySet.has(g.set_id)) {
        bySet.set(g.set_id, { kind: g.set_kind, members: [], hashes: new Set() });
      }
      const bucket = bySet.get(g.set_id);
      for (const m of g.members) bucket.members.push(m);
      bucket.hashes.add(g.hash);
    }
    for (const [setId, bucket] of bySet.entries()) {
      const visible = bucket.members.filter((m) => !m.hidden_overflow);
      if (visible.length < 2) continue;
      const lanes = visible.map((m) => m.lane);
      groups.push({
        kind: bucket.kind,
        set_id: setId,
        depth,
        members: visible,
        lane_min: Math.min(...lanes),
        lane_max: Math.max(...lanes),
      });
    }
  }

  // Hidden-overflow count per depth, for "+N more" pills.
  const overflowByDepth = {};
  for (const member of memberByKey.values()) {
    if (member.hidden_overflow) {
      overflowByDepth[member.depth] = (overflowByDepth[member.depth] || 0) + 1;
    }
  }

  // Build breadcrumb (root → current).
  const path = buildPath(memberByKey, rows, currentHashId, currentOrder);

  // Final node list.
  const nodes = Array.from(memberByKey.values()).filter((m) => !m.hidden_overflow);
  // Stable order for rendering: depth ASC, lane ASC.
  nodes.sort((a, b) => {
    if (a.depth !== b.depth) return a.depth - b.depth;
    return a.lane - b.lane;
  });

  return {
    root_hash_id: rootHashId,
    root_order: rootOrders[0] || 1,
    current_hash_id: currentHashId,
    current_order: currentOrder,
    nodes,
    edges,
    groups,
    overflow_by_depth: overflowByDepth,
    path,
    ghost_hash_ids: Array.from(ghostNodes),
    truncated,
  };
}

function pickThumbUrl(row, order) {
  if (!row) return null;
  if (!Array.isArray(row.images)) return null;
  const img = row.images.find((i) => i.order === order) || row.images[0];
  if (!img) return null;
  return img.thumb_url || null;
}

function findFreeRange(used, preferred, span) {
  // Try the preferred slot first, then expanding outward until we find
  // ``span`` consecutive free lanes inside [0, MAX_LANES).
  const candidates = [];
  for (let d = 0; d < MAX_LANES; d++) {
    if (preferred + d <= MAX_LANES - span) candidates.push(preferred + d);
    if (d > 0 && preferred - d >= 0 && preferred - d <= MAX_LANES - span) {
      candidates.push(preferred - d);
    }
  }
  for (const start of candidates) {
    let ok = true;
    for (let k = 0; k < span; k++) {
      if (used[start + k]) { ok = false; break; }
    }
    if (ok) return start;
  }
  return -1;
}

function buildPath(memberByKey, rows, hashId, currentOrder) {
  // Walk from current up via parent_hash_id / parent_order until root.
  const out = [];
  let cursorHash = hashId;
  let cursorOrder = currentOrder;
  const visited = new Set();
  while (cursorHash) {
    const key = makeMemberKey(cursorHash, cursorOrder);
    if (visited.has(key)) break;
    visited.add(key);
    const member = memberByKey.get(key)
      || memberByKey.get(makeMemberKey(cursorHash, 1));
    if (!member) break;
    out.unshift({
      hash_id: member.hash_id,
      img_order: member.img_order,
      seq_no: member.seq_no,
      is_create_set_member: member.is_create_set_member,
      is_current: member.is_current,
    });
    if (!member.parent_hash_id) break;
    cursorHash = member.parent_hash_id;
    cursorOrder = member.parent_order ?? 1;
  }
  return out;
}

// Helper accessible to UI: format the "#147" or "#147-3" label.
export function formatNodeLabel(member) {
  if (!member) return "#?";
  if (member.is_ghost || member.seq_no == null) return "#?";
  if (member.is_create_set_member) {
    return `#${member.seq_no}-${member.img_order}`;
  }
  return `#${member.seq_no}`;
}

export function formatShortHash(hashId) {
  return shortHash(hashId);
}

void _SETID_BATCH_MAP; // reserved for future memoisation
