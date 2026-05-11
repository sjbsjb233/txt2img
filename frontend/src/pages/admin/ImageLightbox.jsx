import { useEffect } from "react";
import { getApiBase } from "../../api/client.js";

function absoluteImageUrl(url) {
  if (!url) return url;
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  return getApiBase().replace(/\/+$/, "") + url;
}

export default function ImageLightbox({ image, onClose }) {
  useEffect(() => {
    if (!image) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [image, onClose]);

  if (!image) return null;

  return (
    <div
      role="dialog"
      aria-label="Test image preview"
      onClick={onClose}
      data-test="lightbox"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 60,
        background: "rgba(0,0,0,0.65)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <img
        src={absoluteImageUrl(image.bytes_url)}
        alt={image.name}
        style={{
          maxWidth: "90vw",
          maxHeight: "82vh",
          border: "2px solid var(--ink)",
          background: "var(--paper-2)",
        }}
        onClick={(e) => e.stopPropagation()}
      />
      <div
        className="mono"
        style={{
          background: "var(--paper)",
          border: "1px solid var(--ink)",
          padding: "6px 12px",
          fontSize: 11,
          color: "var(--ink)",
          display: "flex",
          gap: 12,
          alignItems: "center",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <span>{image.width}×{image.height}</span>
        <span>·</span>
        <span>{image.mime}</span>
        <span>·</span>
        <span>{Math.round((image.byte_size || 0) / 1024)}KB</span>
        <button type="button" className="btn sm" onClick={onClose} style={{ marginLeft: 6 }}>
          关闭 (Esc)
        </button>
      </div>
    </div>
  );
}
