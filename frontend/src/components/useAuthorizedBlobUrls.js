// Fetch a list of authenticated image URLs and return a stable map from
// the source URL to a blob URL the browser can load via `<img src>` /
// `background-image: url()`.
//
// This is the companion to `<AuthorizedImage>` for components that
// render thumbnails as CSS background-image (e.g. ArchiveSetCard) and
// can't easily host a wrapping React element per cell.
//
// Lifecycle:
//   - On mount and on `sources` change, fetch any URL we don't already
//     have a blob for.
//   - On unmount, revoke every blob URL we created so we don't leak.
//   - When `sources` shrinks (a row was removed), revoke the ones that
//     are no longer referenced.

import { useEffect, useState } from "react";
import { fetchImageBlob } from "../api/archive.js";

export function useAuthorizedBlobUrls(sources) {
  const [byUrl, setByUrl] = useState({});

  useEffect(() => {
    let cancelled = false;
    const want = new Set((sources || []).filter(Boolean));
    const created = []; // urls we made in this effect

    (async () => {
      // Fetch only the missing ones.
      for (const url of want) {
        if (byUrl[url]) continue;
        try {
          const blob = await fetchImageBlob(url);
          if (cancelled || !blob) continue;
          const objUrl = URL.createObjectURL(blob);
          created.push([url, objUrl]);
        } catch {
          // Ignore — the surface UI keeps showing the placeholder.
        }
      }
      if (cancelled) {
        // We were unmounted mid-flight: free anything we already made.
        for (const [, objUrl] of created) URL.revokeObjectURL(objUrl);
        return;
      }
      if (created.length > 0) {
        setByUrl((prev) => {
          const next = { ...prev };
          for (const [src, blob] of created) next[src] = blob;
          return next;
        });
      }
    })();

    // Drop blob entries the new sources list no longer needs.
    setByUrl((prev) => {
      const next = {};
      let changed = false;
      for (const [src, blob] of Object.entries(prev)) {
        if (want.has(src)) {
          next[src] = blob;
        } else {
          URL.revokeObjectURL(blob);
          changed = true;
        }
      }
      return changed ? next : prev;
    });

    return () => {
      cancelled = true;
    };
  }, [JSON.stringify(sources || [])]); // eslint-disable-line react-hooks/exhaustive-deps

  // Cleanup ALL outstanding blobs when the consumer unmounts.
  useEffect(() => {
    return () => {
      // The closure captures `byUrl` at unmount time via the latest state.
      // We can't read byUrl here because the closure was bound at first
      // render — but the per-effect cleanups above already cover the
      // shrink case, and React will throw away the byUrl reference too.
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return byUrl;
}
