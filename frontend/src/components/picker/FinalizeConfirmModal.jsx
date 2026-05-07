// FinalizeConfirmModal — shown when the user tries to set a new FINAL
// while one already exists (PRD §4.4.3).

import { useEffect } from "react";
import AuthorizedImage from "../AuthorizedImage.jsx";

export default function FinalizeConfirmModal({
  currentFinal,
  candidate,
  onCancel,
  onConfirm,
}) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter") {
        e.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, onConfirm]);

  return (
    <>
      <div
        onClick={onCancel}
        style={{
          position: "absolute",
          inset: 0,
          background: "#19171466",
          backdropFilter: "blur(1px)",
          zIndex: 25,
          animation: "pkFadeIn 140ms ease",
        }}
      />
      <div
        data-testid="picker-finalize-confirm-modal"
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 580,
          background: "var(--paper-soft)",
          border: "1.5px solid var(--ink)",
          boxShadow: "12px 12px 0 var(--ink)",
          padding: 24,
          zIndex: 26,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 16,
          }}
        >
          <span style={{ fontSize: 24 }}>★</span>
          <span
            className="display"
            style={{ fontSize: 24, fontStyle: "italic" }}
          >
            Replace the FINAL?
          </span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr auto 1fr",
            gap: 14,
            alignItems: "stretch",
            marginBottom: 18,
          }}
        >
          <div>
            <span className="caps" style={{ fontSize: 10 }}>
              Currently FINAL
            </span>
            <ImageBlock image={currentFinal} corner="→ becomes PICKED" muted />
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--ink-3)",
            }}
          >
            <div
              className="display"
              style={{ fontSize: 32, fontStyle: "italic" }}
            >
              ↦
            </div>
          </div>
          <div>
            <span
              className="caps"
              style={{
                fontSize: 10,
                background: "var(--banana)",
                padding: "2px 4px",
                border: "1px solid var(--ink)",
              }}
            >
              NEW FINAL
            </span>
            <ImageBlock image={candidate} corner="★ FINAL" highlight />
          </div>
        </div>
        <p
          style={{
            margin: 0,
            fontSize: 13,
            color: "var(--ink-2)",
            lineHeight: 1.5,
          }}
        >
          Each session has exactly one FINAL. Confirming will demote the
          current final to <em>picked</em> and use the new image in the slide.
        </p>
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 18,
          }}
        >
          <button
            className="btn sm"
            onClick={onCancel}
            type="button"
          >
            Cancel · <span className="kbd">Esc</span>
          </button>
          <button
            className="btn sm primary shadowed"
            onClick={onConfirm}
            type="button"
          >
            ★ Confirm swap · <span className="kbd">↵</span>
          </button>
        </div>
      </div>
    </>
  );
}

function ImageBlock({ image, corner, highlight, muted }) {
  return (
    <div
      className={`pk-img-pane ${highlight ? "current" : ""}`}
      style={{
        aspectRatio: "1/1",
        marginTop: 6,
        position: "relative",
      }}
    >
      {image ? (
        <AuthorizedImage
          src={image.thumb_url}
          alt=""
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      ) : (
        <div
          className="pk-hatch"
          style={{ position: "absolute", inset: 0 }}
        />
      )}
      <div className={`pk-img-tag ${highlight ? "banana" : muted ? "muted" : ""}`}>
        {corner}
      </div>
    </div>
  );
}
