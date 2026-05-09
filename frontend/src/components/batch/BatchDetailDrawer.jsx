import { useEffect, useState } from "react";

import Icon from "../Icon.jsx";
import { getBatch } from "../../api/batches.js";
import { STATUS_TONE } from "./RunningBatchCard.jsx";

function relativeTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

function SlotProgressRow({ slot, expanded, onToggle }) {
  const failedColor = slot.failed > 0 ? "var(--bad)" : "var(--ink-2)";
  const allSucceeded = slot.failed === 0 && slot.succeeded === slot.image_count;
  return (
    <div
      style={{ borderBottom: "1px solid var(--rule-2)" }}
      data-testid="slot-row"
    >
      <button
        onClick={onToggle}
        style={{
          width: "100%",
          padding: "8px 12px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: expanded ? "var(--paper-2)" : "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--ink-3)",
            width: 12,
            flexShrink: 0,
          }}
        >
          {expanded ? "▾" : "▸"}
        </span>
        <span
          className="mono"
          style={{
            fontSize: 9,
            fontWeight: 700,
            color: "var(--banana-deep)",
            background: "var(--ink)",
            padding: "1px 5px",
            flexShrink: 0,
          }}
        >
          #{String(slot.stable_idx).padStart(2, "0")}
        </span>
        <span
          style={{
            flex: 1,
            fontSize: 12,
            fontWeight: 600,
            color: "var(--ink)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {slot.title}
        </span>
        <span
          className="mono"
          style={{
            fontSize: 10,
            color: allSucceeded ? "var(--ok)" : failedColor,
          }}
        >
          {slot.succeeded}/{slot.image_count}
          {slot.failed > 0 && (
            <span style={{ color: "var(--bad)" }}>
              {" "}
              · {slot.failed} fail
            </span>
          )}
          {allSucceeded && " ✓"}
        </span>
      </button>
      {expanded && (
        <div
          style={{
            padding: "4px 14px 10px 38px",
            background: "var(--paper-2)",
          }}
        >
          {slot.job_hash_ids.length === 0 && (
            <div
              className="mono"
              style={{ fontSize: 11, color: "var(--ink-4)" }}
            >
              No jobs bound yet.
            </div>
          )}
          {slot.job_hash_ids.map((hashId, i) => (
            <div
              key={hashId}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "3px 0",
                borderBottom:
                  i < slot.job_hash_ids.length - 1
                    ? "1px dotted var(--rule-2)"
                    : "none",
              }}
            >
              <span
                className="mono"
                style={{
                  fontSize: 10,
                  color: "var(--ink-3)",
                  width: 36,
                }}
              >
                img {i + 1}:
              </span>
              <span
                className="mono"
                style={{
                  fontSize: 11,
                  color: "var(--ink-2)",
                  flex: 1,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {hashId}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function BatchDetailDrawer({ batchId, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [expandedSlots, setExpandedSlots] = useState(() => new Set());

  useEffect(() => {
    if (!batchId) return undefined;
    let cancelled = false;
    setError(null);
    setData(null);
    (async () => {
      try {
        const fresh = await getBatch(batchId);
        if (!cancelled) setData(fresh);
      } catch (e) {
        if (!cancelled) setError(e?.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  if (!batchId) return null;
  const tone = data ? STATUS_TONE[data.status] : null;
  const startedAgo = data ? relativeTime(data.created_at) : "";

  return (
    <div
      data-testid="batch-detail-drawer"
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: 480,
        background: "#fffdf7",
        borderLeft: "2px solid var(--ink)",
        boxShadow: "-8px 0 0 #00000010",
        display: "flex",
        flexDirection: "column",
        zIndex: 30,
      }}
    >
      <div
        style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--ink)",
          background: tone ? tone.bg : "var(--paper-2)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              minWidth: 0,
            }}
          >
            {tone && (
              <span
                style={{
                  width: 8,
                  height: 8,
                  background: tone.dot,
                  borderRadius:
                    data?.status === "running" ||
                    data?.status === "submitting"
                      ? 0
                      : "50%",
                }}
              />
            )}
            <span
              className="display"
              style={{
                fontSize: 18,
                fontWeight: 800,
                letterSpacing: "-0.02em",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                fontFamily: "var(--font-display)",
              }}
            >
              {data?.title || "Loading…"}
            </span>
            {tone && (
              <span
                className="mono caps"
                style={{
                  fontSize: 9,
                  color: "var(--ink-2)",
                  fontWeight: 700,
                  flexShrink: 0,
                }}
              >
                · {tone.label}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            data-testid="drawer-close"
            style={{
              width: 26,
              height: 26,
              border: "1px solid var(--ink)",
              background: "#fffdf7",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Icon name="close" size={11} />
          </button>
        </div>
        {data && (
          <>
            <div
              className="mono"
              style={{
                fontSize: 10,
                color: "var(--ink-3)",
                marginTop: 6,
              }}
            >
              Started {startedAgo} · GET /api/batches/{data.batch_id}
            </div>
            <div style={{ display: "flex", gap: 14, marginTop: 8 }}>
              <span>
                <span
                  className="ticker"
                  style={{
                    fontSize: 18,
                    fontWeight: 900,
                    fontFamily: "var(--font-display)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {data.succeeded_count}
                </span>{" "}
                <span
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  succeeded
                </span>
              </span>
              <span>
                <span
                  className="ticker"
                  style={{
                    fontSize: 18,
                    fontWeight: 900,
                    color: "var(--bad)",
                    fontFamily: "var(--font-display)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {data.failed_count}
                </span>{" "}
                <span
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  failed
                </span>
              </span>
              <span>
                <span
                  className="ticker"
                  style={{
                    fontSize: 18,
                    fontWeight: 900,
                    color: "var(--banana-deep)",
                    fontFamily: "var(--font-display)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {data.in_flight_count}
                </span>{" "}
                <span
                  className="mono caps"
                  style={{ fontSize: 9, color: "var(--ink-3)" }}
                >
                  in flight
                </span>
              </span>
            </div>
          </>
        )}
        {error && (
          <div
            className="mono"
            style={{
              fontSize: 11,
              color: "var(--bad)",
              marginTop: 6,
            }}
          >
            {error}
          </div>
        )}
      </div>

      <div
        className="mono caps"
        style={{
          fontSize: 10,
          color: "var(--ink-3)",
          padding: "10px 14px 4px",
        }}
      >
        Slots
      </div>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          borderTop: "1px solid var(--rule-2)",
        }}
      >
        {data?.slots?.map((s) => (
          <SlotProgressRow
            key={s.stable_idx}
            slot={s}
            expanded={expandedSlots.has(s.stable_idx)}
            onToggle={() => {
              setExpandedSlots((prev) => {
                const next = new Set(prev);
                if (next.has(s.stable_idx)) next.delete(s.stable_idx);
                else next.add(s.stable_idx);
                return next;
              });
            }}
          />
        ))}
      </div>

      <div
        className="mono"
        style={{
          padding: "6px 16px",
          fontSize: 9,
          color: "var(--ink-4)",
          borderTop: "1px solid var(--rule-2)",
          background: "var(--paper)",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>SSE · live · job_state + image_done</span>
        <span>
          last update{" "}
          {data ? relativeTime(data.updated_at) : "—"}
        </span>
      </div>
    </div>
  );
}
