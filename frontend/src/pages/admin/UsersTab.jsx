import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "../../components/Icon.jsx";
import * as adminUsers from "../../api/admin/users.js";
import { useAuth } from "../../store/auth.js";
import { TierPill } from "./atoms.jsx";
import UserDetailDrawer from "./UserDetailDrawer.jsx";
import NewUserDialog from "./NewUserDialog.jsx";
import BulkPatchDialog from "./BulkPatchDialog.jsx";

// Layout shared with the focused drawer atoms. The selection checkbox
// lives in the leftmost column (40px) so the rest of the row mirrors
// the original mock 1:1.
const COL_TEMPLATE = "32px 240px 110px 110px 1fr 130px 110px 90px";

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso) {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  if (ms < 60_000) return "just now";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function quotaTone(today, soft, hard) {
  if (today >= hard) return "var(--bad)";
  if (today >= soft) return "var(--warn)";
  return "var(--ink)";
}

// ---------------------------------------------------------------------------
// UsersTab
// ---------------------------------------------------------------------------

export default function UsersTab() {
  const { user: currentUser } = useAuth();
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Search / filter / sort / paging
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const [tier, setTier] = useState("all");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("last_login_at:desc");
  const [page, setPage] = useState(1);

  // Selection / drawer / dialogs
  const [selected, setSelected] = useState(() => new Set());
  const [focusedId, setFocusedId] = useState(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);

  // Debounce the search input — admins type fast and 1 req per keystroke
  // is wasteful when the backend has to scan the user table.
  const qTimer = useRef(null);
  useEffect(() => {
    if (qTimer.current) clearTimeout(qTimer.current);
    qTimer.current = setTimeout(() => {
      setQ(qInput);
      setPage(1);
    }, 250);
    return () => {
      if (qTimer.current) clearTimeout(qTimer.current);
    };
  }, [qInput]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminUsers.listUsers({
        q,
        tier: tier === "all" ? undefined : tier,
        status: status === "all" ? undefined : status,
        sort,
        page,
        pageSize: PAGE_SIZE,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(err.message || "Failed to load users.");
    } finally {
      setLoading(false);
    }
  }, [q, tier, status, sort, page]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const showingFrom = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const showingTo = Math.min(total, page * PAGE_SIZE);

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelected((prev) => {
      const allOnPage = items.map((u) => u.id);
      const everySelected = allOnPage.every((id) => prev.has(id));
      if (everySelected) {
        const next = new Set(prev);
        for (const id of allOnPage) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const id of allOnPage) next.add(id);
      return next;
    });
  };

  const clearSelection = () => setSelected(new Set());

  const onAfterChange = async () => {
    await refresh();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* ---------- toolbar ---------- */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: 1, minWidth: 280, maxWidth: 360 }}>
          <input
            className="inp"
            placeholder="search username or display name"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            style={{ paddingLeft: 32, fontFamily: "var(--font-mono)" }}
          />
          <div style={{ position: "absolute", top: 11, left: 10 }}>
            <Icon name="search" size={14} stroke="var(--ink-3)" />
          </div>
        </div>
        <select
          className="inp"
          value={tier}
          onChange={(e) => {
            setTier(e.target.value);
            setPage(1);
          }}
          style={{ width: 140, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="all">tier · all</option>
          <option value="vip">vip</option>
          <option value="premium">premium</option>
          <option value="standard">standard</option>
          <option value="free">free</option>
        </select>
        <select
          className="inp"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
          style={{ width: 160, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="all">status · active+disabled</option>
          <option value="active">active</option>
          <option value="disabled">disabled</option>
          <option value="deleted">deleted</option>
        </select>
        <select
          className="inp"
          value={sort}
          onChange={(e) => {
            setSort(e.target.value);
            setPage(1);
          }}
          style={{ width: 220, fontFamily: "var(--font-mono)", fontSize: 12 }}
        >
          <option value="last_login_at:desc">sort · last_login_at desc</option>
          <option value="created_at:desc">sort · created_at desc</option>
          <option value="today_count:desc">sort · today_count desc</option>
          <option value="username:asc">sort · username asc</option>
        </select>
        <div style={{ flex: 1 }} />
        <button
          className="btn"
          disabled={selected.size === 0}
          onClick={() => setBulkOpen(true)}
        >
          <Icon name="upload" size={13} /> Bulk · {selected.size}
        </button>
        <button className="btn primary shadowed" onClick={() => setNewOpen(true)}>
          <Icon name="plus" size={13} /> New user
        </button>
      </div>

      {/* ---------- error banner ---------- */}
      {error && (
        <div
          style={{
            padding: 10,
            border: "1px solid var(--bad)",
            color: "var(--bad)",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      {/* ---------- table ---------- */}
      <div style={{ border: "1px solid var(--ink)", background: "#fffdf7" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: COL_TEMPLATE,
            gap: 12,
            padding: "10px 16px",
            background: "var(--ink)",
            color: "var(--paper)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            fontWeight: 700,
            alignItems: "center",
          }}
        >
          <div>
            <input
              type="checkbox"
              aria-label="select all"
              checked={
                items.length > 0 && items.every((u) => selected.has(u.id))
              }
              onChange={toggleSelectAll}
            />
          </div>
          <div>USER</div>
          <div>ROLE</div>
          <div>TIER</div>
          <div>TODAY USAGE (soft / hard)</div>
          <div>LAST LOGIN</div>
          <div>30D JOBS</div>
          <div style={{ textAlign: "right" }}>—</div>
        </div>

        {loading && items.length === 0 && (
          <div
            className="mono"
            style={{
              padding: 18,
              color: "var(--ink-3)",
              fontSize: 12,
              fontStyle: "italic",
            }}
          >
            loading users…
          </div>
        )}

        {!loading && items.length === 0 && (
          <div
            className="mono"
            style={{
              padding: 18,
              color: "var(--ink-3)",
              fontSize: 12,
              fontStyle: "italic",
            }}
          >
            no users match this filter
          </div>
        )}

        {items.map((u, i) => {
          const today = u.today_count;
          const soft = u.soft_quota_effective;
          const hard = u.hard_quota_effective;
          const pct = (today / Math.max(1, hard)) * 100;
          const softPct = (soft / Math.max(1, hard)) * 100;
          const tone = quotaTone(today, soft, hard);
          const softBreached = today >= soft && today < hard;
          const hardNear = today >= hard;
          const isFocused = focusedId === u.id;
          return (
            <div
              key={u.id}
              data-user-row={u.id}
              onClick={() => setFocusedId(u.id)}
              style={{
                display: "grid",
                gridTemplateColumns: COL_TEMPLATE,
                gap: 12,
                padding: "12px 16px",
                alignItems: "center",
                borderTop: i ? "1px solid var(--rule)" : "none",
                background: isFocused
                  ? "var(--paper-3)"
                  : u.status === "disabled"
                    ? "var(--paper-2)"
                    : "#fffdf7",
                opacity: u.status === "disabled" ? 0.7 : 1,
                cursor: "pointer",
              }}
            >
              <div onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  aria-label={`select ${u.username}`}
                  checked={selected.has(u.id)}
                  onChange={() => toggleSelect(u.id)}
                />
              </div>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div
                    style={{
                      width: 26,
                      height: 26,
                      background:
                        u.role === "admin" ? "var(--ink)" : "var(--paper-3)",
                      color: u.role === "admin" ? "var(--banana)" : "var(--ink)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      fontWeight: 700,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      border: "1px solid var(--ink)",
                      flexShrink: 0,
                    }}
                  >
                    {u.username.slice(0, 2).toUpperCase()}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.2 }}>
                      {u.display_name || u.username}
                    </div>
                    <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                      @{u.username}
                    </div>
                  </div>
                </div>
              </div>
              <div>
                {u.role === "admin" ? (
                  <span className="chip solid" style={{ fontSize: 9 }}>
                    ADMIN
                  </span>
                ) : (
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                    user
                  </span>
                )}
              </div>
              <div>
                <TierPill tier={u.tier} />
              </div>
              <div>
                <div
                  style={{
                    height: 6,
                    background: "var(--paper-3)",
                    position: "relative",
                    border: "1px solid var(--ink)",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: `${Math.min(100, pct)}%`,
                      background: tone,
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      left: `${Math.min(100, softPct)}%`,
                      top: -2,
                      bottom: -2,
                      width: 1,
                      background: "var(--ink)",
                    }}
                  />
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 4 }}
                >
                  <span style={{ color: tone, fontWeight: 700 }}>{today}</span> /
                  soft {soft} / hard {hard}
                  {hardNear && (
                    <span style={{ marginLeft: 6, color: "var(--bad)" }}>
                      · hard reached
                    </span>
                  )}
                  {!hardNear && softBreached && (
                    <span style={{ marginLeft: 6, color: "var(--warn)" }}>
                      · soft breach
                    </span>
                  )}
                </div>
              </div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                {relativeTime(u.last_login_at)}
              </div>
              <div className="mono" style={{ fontSize: 12, fontWeight: 700 }}>
                {u.jobs_30d_total.toLocaleString()}
              </div>
              <div
                style={{ display: "flex", justifyContent: "flex-end", gap: 4 }}
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  className="btn sm ghost"
                  title="Focus"
                  onClick={() => setFocusedId(u.id)}
                >
                  <Icon name="eye" size={12} />
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* ---------- pager ---------- */}
      <div
        className="mono"
        style={{
          fontSize: 11,
          color: "var(--ink-3)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>
          showing {showingFrom}–{showingTo} of {total}
          {selected.size > 0 && (
            <>
              {" · "}
              <button
                className="btn sm ghost"
                onClick={clearSelection}
                style={{ display: "inline-flex" }}
              >
                clear selection ({selected.size})
              </button>
            </>
          )}
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            className="btn sm ghost"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
          >
            prev
          </button>
          <span>
            page {page} / {totalPages}
          </span>
          <button
            className="btn sm ghost"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
          >
            next
          </button>
        </span>
      </div>

      {/* ---------- focused drawer ---------- */}
      {focusedId && (
        <UserDetailDrawer
          userId={focusedId}
          currentAdminId={currentUser?.id}
          onClose={() => setFocusedId(null)}
          onChanged={onAfterChange}
        />
      )}

      {/* ---------- dialogs ---------- */}
      <NewUserDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={() => {
          setNewOpen(false);
          void refresh();
        }}
      />
      <BulkPatchDialog
        open={bulkOpen}
        ids={Array.from(selected)}
        onClose={() => setBulkOpen(false)}
        onApplied={() => {
          setBulkOpen(false);
          clearSelection();
          void refresh();
        }}
      />
    </div>
  );
}
