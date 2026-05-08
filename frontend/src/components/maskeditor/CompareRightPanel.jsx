import { SectionHeader } from "./RightPanel/atoms.jsx";

export default function CompareRightPanel({ result, parent, derivedVersions, onVersionPick }) {
  const dollars = result?.cost?.dollars;
  return (
    <div className="me-right-panel" data-testid="me-compare-right">
      <div style={{ padding: 16, borderBottom: "1px solid var(--rule)", background: "var(--paper-2)" }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.14em" }}>
          result
        </div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 700 }}>
          #{result?.seq_no || "—"}
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
          derived from {parent ? `#${parent.seq_no}` : "—"} ·{" "}
          {result?.timing?.render_seconds
            ? `${result.timing.render_seconds.toFixed(1)}s`
            : "—"}
          {" · "}
          {result?.images?.length || 0} image
        </div>
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
        <FieldL label="prompt">{result?.prompt || "—"}</FieldL>
        <FieldL label="model">
          {result?.model} · {result?.params?.quality || "auto"} · {result?.params?.output_format || "png"}
        </FieldL>
        {dollars != null && (
          <FieldL label="cost">
            ${dollars.toFixed(3)}
            {result.cost.input_tokens != null && (
              <span style={{ color: "var(--ink-3)" }}>
                {" · "}
                {result.cost.input_tokens} in + {result.cost.output_tokens || 0} out tokens
              </span>
            )}
          </FieldL>
        )}

        <div className="me-derived-card">
          <div className="me-derived-card__title">
            versions of #{parent?.seq_no || "—"} · {derivedVersions.length} derived
          </div>
          {derivedVersions.length === 0 && (
            <div style={{ padding: 6, color: "var(--ink-3)", fontSize: 11 }}>
              no other versions yet — first edit lives here.
            </div>
          )}
          {derivedVersions.map((v, i) => {
            const active = v.hash_id === result?.hash_id;
            return (
              <div
                key={v.hash_id}
                className={`me-derived-row ${active ? "me-derived-row--active" : ""}`}
                onClick={() => !active && onVersionPick?.(v)}
              >
                <div className={`me-derived-thumb ${active ? "me-derived-thumb--active" : ""}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700 }}>
                    #{v.seq_no}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--ink-2)" }}>
                    {v.derivation_kind || "edit"}
                  </div>
                </div>
                {active && (
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--ink-2)", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.14em" }}>
                    now
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function FieldL({ label, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.45 }}>{children}</div>
    </div>
  );
}
