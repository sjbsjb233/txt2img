// User-facing announcements banner stack (FE-08 / design doc §11).
//
// Two display modes per design:
//   - priority >= 5  → modal (full-screen blocker until dismissed)
//   - priority <  5  → top-of-page banner (stacked vertically)
//
// Non-dismissable announcements omit the close button. The frontend
// trusts the backend to flip ``dismissable=false`` only on truly
// must-read content (maintenance windows, etc).

import { coverUrl } from "../api/announcements.js";
import * as announcementsStore from "../store/announcements.js";

// ---------------------------------------------------------------------
// Markdown-lite renderer for text announcements
// ---------------------------------------------------------------------
//
// Design doc §11.1 calls for sanitized markdown for ``content_kind="text"``
// content. v1 keeps it minimal — paragraphs separated by blank lines,
// **bold**, *italic*, [text](url) — to avoid pulling in a markdown
// library + a sanitizer just for banner copy. Anything more elaborate
// is the admin's signal to use `content_kind="image"` with a designed
// asset.

function renderInline(segment) {
  // Process **bold** first so it doesn't get nested into italic.
  const out = [];
  let lastIdx = 0;
  const re = /(\*\*([^*]+)\*\*|\*([^*]+)\*|\[([^\]]+)\]\(([^)]+)\))/g;
  let match;
  let key = 0;
  while ((match = re.exec(segment)) !== null) {
    if (match.index > lastIdx) {
      out.push(segment.slice(lastIdx, match.index));
    }
    if (match[2]) {
      out.push(<strong key={`b-${key++}`}>{match[2]}</strong>);
    } else if (match[3]) {
      out.push(<em key={`i-${key++}`}>{match[3]}</em>);
    } else if (match[4] && match[5]) {
      // Links must be http/https — never auto-render javascript: URLs.
      const href = /^https?:\/\//i.test(match[5]) ? match[5] : "#";
      out.push(
        <a
          key={`l-${key++}`}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--ink)", textDecoration: "underline" }}
        >
          {match[4]}
        </a>,
      );
    }
    lastIdx = re.lastIndex;
  }
  if (lastIdx < segment.length) {
    out.push(segment.slice(lastIdx));
  }
  return out;
}

function renderTextContent(text) {
  if (!text) return null;
  const paragraphs = text.split(/\n{2,}/);
  return paragraphs.map((p, i) => (
    <p key={i} style={{ margin: i === 0 ? 0 : "10px 0 0", lineHeight: 1.5 }}>
      {renderInline(p.replace(/\n/g, " "))}
    </p>
  ));
}

// ---------------------------------------------------------------------
// Single banner card
// ---------------------------------------------------------------------

function CloseButton({ onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Dismiss"
      style={{
        background: "transparent",
        border: "none",
        cursor: "pointer",
        fontSize: 22,
        lineHeight: 1,
        padding: "2px 8px",
        color: "var(--ink-2)",
      }}
    >
      ×
    </button>
  );
}

function BannerCard({ ann, variant }) {
  const handleDismiss = () => {
    if (!ann.dismissable) return;
    announcementsStore.dismiss(ann.id);
  };

  const isImage = ann.content_kind === "image";
  const cover = isImage ? coverUrl(ann.id) : null;

  if (variant === "modal") {
    return (
      <div
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15, 15, 15, 0.45)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
          padding: 24,
        }}
        // Click outside dismisses only if the announcement is dismissable.
        onClick={ann.dismissable ? handleDismiss : undefined}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            maxWidth: 520,
            width: "100%",
            background: "#fffdf7",
            border: "1px solid var(--ink)",
            boxShadow: "8px 8px 0 var(--ink)",
            padding: 24,
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-start",
              gap: 12,
            }}
          >
            <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              ANNOUNCEMENT · P{ann.priority}
            </div>
            {ann.dismissable && <CloseButton onClick={handleDismiss} />}
          </div>
          {ann.title && (
            <div
              className="display"
              style={{
                fontSize: 26,
                fontWeight: 800,
                letterSpacing: "-0.02em",
                lineHeight: 1.1,
              }}
            >
              {ann.title}
            </div>
          )}
          {isImage ? (
            <img
              src={cover}
              alt={ann.title || "Announcement cover"}
              style={{
                width: "100%",
                height: "auto",
                border: "1px solid var(--ink-4)",
              }}
            />
          ) : (
            <div style={{ fontSize: 14, color: "var(--ink-2)" }}>
              {renderTextContent(ann.content)}
            </div>
          )}
          {ann.dismissable && (
            <button
              type="button"
              className="btn primary shadowed"
              onClick={handleDismiss}
              style={{ alignSelf: "flex-end" }}
            >
              Got it
            </button>
          )}
        </div>
      </div>
    );
  }

  // Top-of-page banner variant.
  return (
    <div
      data-announcement-id={ann.id}
      style={{
        background: "#fffdf7",
        borderBottom: "1px solid var(--ink)",
        padding: "10px 20px",
        display: "flex",
        alignItems: isImage ? "center" : "flex-start",
        gap: 14,
      }}
    >
      <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
        NEWS
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {ann.title && (
          <div
            style={{
              fontSize: 13,
              fontWeight: 700,
              color: "var(--ink)",
              marginBottom: isImage ? 0 : 2,
              lineHeight: 1.3,
            }}
          >
            {ann.title}
          </div>
        )}
        {isImage ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <img
              src={cover}
              alt={ann.title || "Announcement"}
              style={{
                height: 32,
                border: "1px solid var(--ink-4)",
                marginRight: 4,
              }}
            />
          </span>
        ) : (
          <div style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.4 }}>
            {renderTextContent(ann.content)}
          </div>
        )}
      </div>
      {ann.dismissable && <CloseButton onClick={handleDismiss} />}
    </div>
  );
}

// ---------------------------------------------------------------------
// Banner stack (mounted in Layout.jsx)
// ---------------------------------------------------------------------

export default function AnnouncementsBanner() {
  const list = announcementsStore.useAnnouncements();
  if (!list || list.length === 0) return null;

  // Modals (priority >= 5) render first so their backdrop covers the
  // banner stack behind them. Only one modal at a time — the highest-
  // priority entry wins; the rest stack as banners until the user
  // dismisses the modal.
  const modals = list.filter((a) => (a.priority ?? 0) >= 5);
  const banners = list.filter((a) => (a.priority ?? 0) < 5);

  const top = modals[0] || null;
  const restAsBanners = modals.slice(1).concat(banners);

  return (
    <>
      {restAsBanners.map((ann) => (
        <BannerCard key={ann.id} ann={ann} variant="banner" />
      ))}
      {top && <BannerCard key={top.id} ann={top} variant="modal" />}
    </>
  );
}
