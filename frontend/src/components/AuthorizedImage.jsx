// `<img>` that fetches its src with the bearer token attached.
//
// Why this exists:
//   - Native `<img src=>` cannot carry an `Authorization` header.
//   - The backend's image endpoints require bearer auth.
//   - In dev the frontend (localhost:15173) is cross-origin from the
//     backend (127.0.0.1:18000), so cookie auth wouldn't work either.
//
// We therefore fetch() the bytes ourselves with the token attached, wrap
// them in an object URL, and hand that to a regular `<img>`. Object URLs
// stay alive until we revoke them; the cleanup runs on unmount and on
// src change so we don't leak blobs.
//
// 404 is treated as "file no longer on the server" and surfaces as an
// `onMissing` callback so the parent can drop the local cache row.

import { useEffect, useRef, useState } from "react";
import { fetchImageBlob } from "../api/archive.js";

export default function AuthorizedImage({
  src,
  alt = "",
  style,
  className,
  onMissing,
  fallback = null,
}) {
  const [blobUrl, setBlobUrl] = useState(null);
  const [missing, setMissing] = useState(false);
  const onMissingRef = useRef(onMissing);

  useEffect(() => {
    onMissingRef.current = onMissing;
  }, [onMissing]);

  useEffect(() => {
    if (!src) {
      setBlobUrl(null);
      setMissing(false);
      return undefined;
    }
    let cancelled = false;
    let url = null;

    setMissing(false);
    setBlobUrl(null);

    (async () => {
      try {
        const blob = await fetchImageBlob(src);
        if (cancelled) return;
        if (blob === null) {
          setMissing(true);
          if (typeof onMissingRef.current === "function") {
            onMissingRef.current();
          }
          return;
        }
        url = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        setBlobUrl(url);
      } catch (err) {
        if (cancelled) return;
        // Treat any non-404 fetch failure as missing too — there's no
        // meaningful UI for a transient network error on a thumbnail
        // since we'd just retry on next render anyway.
        setMissing(true);
      }
    })();

    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [src]);

  if (missing) {
    return fallback;
  }
  if (!blobUrl) {
    // Render an invisible placeholder so the layout doesn't reflow when
    // the bytes arrive. Inherits whatever sizing the parent applied.
    return <div style={style} className={className} aria-busy="true" />;
  }
  return (
    <img
      src={blobUrl}
      alt={alt}
      style={style}
      className={className}
      loading="lazy"
    />
  );
}
