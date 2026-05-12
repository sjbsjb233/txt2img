// Hook that subscribes to ``archiveStore`` and recomputes the lineage
// snapshot whenever the underlying rows change. The selector is a pure
// function so we can rebuild on every notify() without worrying about
// staleness.

import { useEffect, useMemo, useState } from "react";

import * as archiveStore from "../../../store/archive.js";
import { computeLineage } from "./computeLineage.js";

export function useLineage(currentHashId, currentOrder = 1) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    // ``archiveStore.subscribe`` returns its unsubscribe — propagate it
    // so React invokes it on unmount and we don't leak listeners that
    // ``setTick`` after the component is gone.
    const off = archiveStore.subscribe(() => setTick((n) => n + 1));
    return off;
  }, []);

  const lineage = useMemo(() => {
    if (!currentHashId) return null;
    // ``getRows`` returns the rows sorted; we need the Map for the
    // selector. The internal map isn't exposed, so we reassemble it.
    const list = archiveStore.getRows();
    const rows = new Map();
    for (const r of list) {
      if (r?.hash_id) rows.set(r.hash_id, r);
    }
    return computeLineage(rows, currentHashId, { currentOrder });
    // ``tick`` participates so the memo refreshes on every store notify.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, currentHashId, currentOrder]);

  // Background: when the lineage references ghost (= missing) ancestors,
  // ask the store to refresh them so the next compute fills them in.
  useEffect(() => {
    if (!lineage?.ghost_hash_ids?.length) return;
    let cancelled = false;
    (async () => {
      for (const hid of lineage.ghost_hash_ids) {
        if (cancelled) return;
        try {
          await archiveStore.refreshDetail(hid);
        } catch {
          // ignore — ghosts may genuinely be deleted.
        }
      }
    })();
    return () => { cancelled = true; };
  }, [lineage?.ghost_hash_ids?.join(",") || ""]);

  return lineage;
}
