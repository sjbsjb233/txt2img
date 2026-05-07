// Fullscreen mode (PRD §4.5) — distraction-free dual pane on dark backdrop.

import AuthorizedImage from "../AuthorizedImage.jsx";
import Filmstrip from "./Filmstrip.jsx";

export default function FullscreenView({
  bundle,
  cursorIdx,
  setCursorIdx,
  onExit,
  inFlightCounts,
}) {
  const images = bundle ? Array.from(bundle.images.values()) : [];
  const cursor = images[cursorIdx];
  const finalImg = bundle?.session?.final_image_id
    ? bundle.images.get(bundle.session.final_image_id)
    : null;

  const judged = images.filter(
    (i) =>
      i.pick_state === "picked" ||
      i.pick_state === "discarded" ||
      i.pick_state === "final"
  ).length;
  const judgedPct = images.length
    ? Math.round((judged / images.length) * 100)
    : 0;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "#0e0d0b",
        color: "var(--paper)",
        display: "grid",
        gridTemplateRows: "auto 1fr auto",
        fontFamily: "var(--font-sans)",
        zIndex: 50,
      }}
    >
      {/* Top bar */}
      <div
        style={{
          padding: "10px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: "1px solid #ffffff15",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span
            className="mono caps"
            style={{
              fontSize: 10,
              color: "var(--banana)",
              letterSpacing: "0.2em",
            }}
          >
            FULLSCREEN · {bundle?.session?.name || "—"}
          </span>
          <span
            className="mono"
            style={{ fontSize: 11, color: "#ffffff80" }}
          >
            #{cursorIdx + 1} / {images.length} · {judgedPct}%
          </span>
        </div>
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <KbdHint k="P" lbl="pick" />
          <KbdHint k="X" lbl="discard" />
          <KbdHint k="F" lbl="final" />
          <KbdHint k="␣" lbl="defer" />
          <KbdHint k="←→" lbl="nav" />
          <KbdHint k="U" lbl="undo" />
          <span style={{ width: 1, height: 18, background: "#ffffff20" }} />
          <button
            onClick={onExit}
            type="button"
            style={{
              all: "unset",
              cursor: "pointer",
              padding: "4px 10px",
              fontSize: 12,
              color: "var(--paper)",
              border: "1px solid #ffffff40",
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.1em",
            }}
          >
            EXIT · Esc
          </button>
        </div>
      </div>

      {/* Two giant panes */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 32,
          padding: 24,
          minHeight: 0,
        }}
      >
        <FsPane label="CURRENT FINAL" image={finalImg} />
        <FsPane
          label={`CANDIDATE · #${cursorIdx + 1}`}
          image={cursor}
          highlight
        />
      </div>

      {/* Bottom filmstrip */}
      <div
        style={{
          background: "#16140f",
          borderTop: "1px solid #ffffff15",
          padding: "10px 24px 12px",
        }}
      >
        <Filmstrip
          images={images}
          cursorIdx={cursorIdx}
          onPick={setCursorIdx}
          inflightR={inFlightCounts.running}
          inflightQ={inFlightCounts.queued}
          inflightF={inFlightCounts.failed}
          height={28}
          padded={false}
          dark
        />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: 6,
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#ffffff60",
            letterSpacing: "0.1em",
          }}
        >
          <span>1</span>
          <span>{Math.max(1, Math.ceil(images.length / 4))}</span>
          <span>{Math.max(1, Math.ceil(images.length / 2))}</span>
          <span>{Math.max(1, Math.ceil((images.length * 3) / 4))}</span>
          <span>{images.length}</span>
        </div>
      </div>
    </div>
  );
}

function KbdHint({ k, lbl }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "#ffffff70",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          minWidth: 18,
          height: 18,
          padding: "0 4px",
          border: "1px solid #ffffff40",
          background: "transparent",
          fontSize: 10,
          fontWeight: 700,
          color: "var(--paper)",
        }}
      >
        {k}
      </span>
      {lbl}
    </span>
  );
}

function FsPane({ label, image, highlight }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minHeight: 0,
        alignItems: "stretch",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          letterSpacing: "0.2em",
          color: highlight ? "var(--banana)" : "#ffffff80",
          padding: "4px 0",
        }}
      >
        {highlight && "▸ "}
        {label}
      </div>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          position: "relative",
          opacity: image ? 1 : 0.4,
        }}
      >
        {image ? (
          <div
            style={{
              height: "100%",
              aspectRatio: `${image.width} / ${image.height}`,
              maxWidth: "100%",
              border: highlight ? "2px solid var(--banana)" : "1px solid #ffffff30",
              boxShadow: highlight
                ? "0 0 0 1px #19171455, 0 20px 60px -20px #00000099"
                : "0 20px 60px -20px #00000099",
              position: "relative",
              overflow: "hidden",
            }}
          >
            <AuthorizedImage
              src={image.thumb_url}
              alt=""
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
              }}
            />
            {highlight && image.pick_state !== "unjudged" && (
              <div
                style={{
                  position: "absolute",
                  top: 10,
                  right: 10,
                  background: "var(--banana)",
                  color: "var(--ink)",
                  padding: "3px 8px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.2em",
                  fontWeight: 700,
                  border: "1px solid var(--ink)",
                }}
              >
                {(image.pick_state || "").toUpperCase()}
              </div>
            )}
          </div>
        ) : (
          <div
            style={{
              width: "60%",
              aspectRatio: "1/1",
              border: "1.5px dashed #ffffff20",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#ffffff40",
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              fontSize: 22,
            }}
          >
            no final yet
          </div>
        )}
      </div>
    </div>
  );
}
