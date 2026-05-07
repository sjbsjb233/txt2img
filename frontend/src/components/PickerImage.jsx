// Authorized image renderer that goes through the picker queue
// (concurrency-capped, LRU-cached). Falls back to a static placeholder
// when the file is missing.

import { useEffect, useRef, useState } from "react";
import * as queue from "../store/pickerImageQueue.js";

export default function PickerImage({
  imageId,
  variant = "thumb", // "thumb" | "full"
  src,
  priority = 50,
  alt = "",
  className,
  style,
  onMissing,
}) {
  const [url, setUrl] = useState(null);
  const [missing, setMissing] = useState(false);
  const onMissingRef = useRef(onMissing);

  useEffect(() => {
    onMissingRef.current = onMissing;
  }, [onMissing]);

  useEffect(() => {
    if (!src || !imageId) {
      setUrl(null);
      setMissing(false);
      return undefined;
    }
    const key = `${imageId}:${variant}`;
    let cancelled = false;
    setMissing(false);
    queue
      .request(key, src, priority)
      .then((next) => {
        if (cancelled) return;
        if (next === null) {
          setMissing(true);
          if (typeof onMissingRef.current === "function") onMissingRef.current();
          return;
        }
        setUrl(next);
      })
      .catch(() => {
        if (cancelled) return;
        setMissing(true);
      });

    return () => {
      cancelled = true;
      queue.release(key);
    };
  }, [imageId, src, variant, priority]);

  if (missing) {
    return (
      <div
        className={className}
        style={{
          ...style,
          background: "var(--paper-3)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--ink-3)",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
        }}
      >
        missing
      </div>
    );
  }
  if (!url) {
    return (
      <div
        className={className}
        style={{ ...style, background: "var(--paper-3)" }}
        aria-busy="true"
      />
    );
  }
  return (
    <img
      src={url}
      alt={alt}
      className={className}
      loading="lazy"
      style={{
        objectFit: "cover",
        width: "100%",
        height: "100%",
        display: "block",
        ...style,
      }}
    />
  );
}
