// Admin announcements editor (FE-07 / design doc §11+§13).
//
// Replaces the mock list with real /api/admin/announcements data.
// Layout matches the existing admin design: a list of cards on the
// left, a compose / edit panel on the right.

import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "../../components/Icon.jsx";
import * as adminAnn from "../../api/admin/announcements.js";

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

const TIER_OPTIONS = ["vip", "premium", "standard", "free"];

function formatTs(value) {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toISOString().slice(0, 16).replace("T", " ");
  } catch {
    return String(value);
  }
}

function isoForDatetimeLocal(date) {
  // <input type="datetime-local"> wants ``YYYY-MM-DDTHH:MM`` in local
  // time. We round-trip through the UTC ISO so the backend always
  // receives an unambiguous moment.
  if (!date) return "";
  const d = new Date(date);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function isoFromDatetimeLocal(value) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

const EMPTY_FORM = {
  title: "",
  content_kind: "text",
  content: "",
  audience_kind: "all",
  audience_data: [],
  audience_user_list_text: "",
  starts_at_local: isoForDatetimeLocal(new Date()),
  ends_at_local: "",
  dismissable: true,
  priority: 5,
  cover_file: null,
};

function audienceLabel(item) {
  if (item.audience_kind === "all") return "all";
  if (item.audience_kind === "tier") {
    return (item.audience_data || []).join(", ") || "(none)";
  }
  if (item.audience_kind === "user_list") {
    const list = item.audience_data || [];
    return list.length === 1 ? list[0] : `${list.length} users`;
  }
  return item.audience_kind;
}

// ---------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------

function AnnouncementCard({ item, onEdit, onDelete }) {
  const stateChip = item.is_live ? "live" : "scheduled";

  return (
    <div
      style={{
        border: "1px solid var(--ink)",
        background: item.is_live ? "#fffdf7" : "var(--paper-2)",
        padding: 16,
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
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <span
              className="display"
              style={{
                fontSize: 18,
                fontWeight: 800,
                letterSpacing: "-0.015em",
              }}
            >
              {item.title || "(no title)"}
            </span>
            <span className={`chip ${item.is_live ? "ok" : ""}`}>
              {stateChip.toUpperCase()}
            </span>
            <span className="chip">P{item.priority}</span>
            <span
              className="chip"
              style={{
                background:
                  item.content_kind === "image"
                    ? "var(--banana-soft)"
                    : "transparent",
              }}
            >
              {item.content_kind}
            </span>
            {!item.dismissable && (
              <span className="chip" style={{ background: "var(--bad)", color: "var(--paper)" }}>
                MUST READ
              </span>
            )}
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}
          >
            {item.id}
          </div>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <button className="btn sm" onClick={() => onEdit(item)}>
            Edit
          </button>
          <button className="btn sm" onClick={() => onDelete(item)}>
            Delete
          </button>
        </div>
      </div>

      <div
        style={{
          marginTop: 10,
          fontSize: 13,
          color: "var(--ink-2)",
          lineHeight: 1.5,
        }}
      >
        {item.content_kind === "text" ? (
          `"${(item.content || "").slice(0, 200)}${item.content && item.content.length > 200 ? "…" : ""}"`
        ) : (
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              style={{
                width: 36,
                height: 36,
                background: "var(--banana)",
                border: "1px solid var(--ink)",
                display: "inline-block",
              }}
            />
            <span className="mono" style={{ fontSize: 11 }}>
              {item.content}
            </span>
          </span>
        )}
      </div>

      <div
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px solid var(--rule)",
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 10,
        }}
      >
        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)" }}
          >
            AUDIENCE
          </div>
          <div className="mono" style={{ fontSize: 11, fontWeight: 700, marginTop: 2 }}>
            {audienceLabel(item)}
          </div>
        </div>
        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)" }}
          >
            REACH
          </div>
          <div className="mono" style={{ fontSize: 11, fontWeight: 700, marginTop: 2 }}>
            {item.reach} users
          </div>
        </div>
        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)" }}
          >
            READ
          </div>
          <div className="mono" style={{ fontSize: 11, fontWeight: 700, marginTop: 2 }}>
            {item.read_count}
            {item.reach > 0 && (
              <span style={{ color: "var(--ink-3)", marginLeft: 4 }}>
                ({Math.round((item.read_count / item.reach) * 100)}%)
              </span>
            )}
          </div>
        </div>
        <div>
          <div
            className="mono caps"
            style={{ fontSize: 9, color: "var(--ink-3)" }}
          >
            WINDOW
          </div>
          <div className="mono" style={{ fontSize: 10, marginTop: 2 }}>
            {formatTs(item.starts_at)}
            <br />
            {item.ends_at ? `→ ${formatTs(item.ends_at)}` : "→ ∞"}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Editor panel
// ---------------------------------------------------------------------

function EditorPanel({ form, setForm, onSubmit, editing, onCancel, busy, error }) {
  const fileInputRef = useRef(null);

  const setField = (name, value) =>
    setForm((prev) => ({ ...prev, [name]: value }));

  const onFileChange = (e) => {
    const file = e.target.files?.[0] || null;
    setField("cover_file", file);
  };

  const audienceList =
    form.audience_kind === "user_list"
      ? form.audience_user_list_text
          .split(/[\s,]+/)
          .map((s) => s.trim())
          .filter(Boolean)
      : form.audience_data;

  return (
    <div
      style={{
        border: "1px solid var(--ink)",
        background: "#fffdf7",
        padding: 18,
        alignSelf: "flex-start",
        boxShadow: "5px 5px 0 var(--ink)",
        position: "sticky",
        top: 0,
      }}
    >
      <div
        className="mono caps"
        style={{ fontSize: 10, color: "var(--ink-3)", letterSpacing: "0.16em" }}
      >
        {editing ? `EDIT · ${editing.id}` : "NEW ANNOUNCEMENT"}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
        style={{
          marginTop: 12,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div>
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            TITLE
          </div>
          <input
            className="inp"
            placeholder="Scheduled maintenance"
            value={form.title || ""}
            onChange={(e) => setField("title", e.target.value)}
            style={{ marginTop: 4 }}
          />
        </div>

        <div>
          <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
            CONTENT KIND
          </div>
          <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
            {["text", "image", "react"].map((k) => {
              const disabled = k === "react" || (editing && editing.content_kind !== k);
              const active = form.content_kind === k;
              return (
                <span
                  key={k}
                  className={`chip ${active ? "solid" : ""}`}
                  style={{
                    cursor: disabled ? "not-allowed" : "pointer",
                    opacity: disabled ? 0.4 : 1,
                  }}
                  onClick={() => {
                    if (disabled) return;
                    setField("content_kind", k);
                  }}
                >
                  {k}
                  {k === "react" ? " · v1.1" : ""}
                </span>
              );
            })}
          </div>
        </div>

        {form.content_kind === "text" ? (
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              CONTENT (markdown)
            </div>
            <textarea
              className="inp"
              placeholder="**Heads up:** the cluster will be down 02:00–03:00 UTC+8."
              value={form.content || ""}
              onChange={(e) => setField("content", e.target.value)}
              style={{ marginTop: 4, height: 120, fontFamily: "var(--font-mono)" }}
            />
          </div>
        ) : (
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              COVER IMAGE
            </div>
            {editing ? (
              <div
                className="mono"
                style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}
              >
                Image content cannot be edited in place — delete and
                re-create to swap covers.
              </div>
            ) : (
              <input
                ref={fileInputRef}
                className="inp"
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                onChange={onFileChange}
                style={{ marginTop: 4 }}
              />
            )}
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              AUDIENCE
            </div>
            <select
              className="inp"
              value={form.audience_kind}
              onChange={(e) => setField("audience_kind", e.target.value)}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="all">all</option>
              <option value="tier">tier</option>
              <option value="user_list">user_list</option>
            </select>
          </div>
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              PRIORITY
            </div>
            <input
              className="inp"
              value={form.priority}
              onChange={(e) =>
                setField("priority", Number(e.target.value) || 0)
              }
              type="number"
              min={-100}
              max={100}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)" }}
            />
          </div>
        </div>

        {form.audience_kind === "tier" && (
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              TIERS
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
              {TIER_OPTIONS.map((t) => {
                const on = form.audience_data.includes(t);
                return (
                  <span
                    key={t}
                    className={`chip ${on ? "solid" : ""}`}
                    style={{ cursor: "pointer" }}
                    onClick={() => {
                      const next = on
                        ? form.audience_data.filter((x) => x !== t)
                        : [...form.audience_data, t];
                      setField("audience_data", next);
                    }}
                  >
                    {t}
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {form.audience_kind === "user_list" && (
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              USER IDS (comma or newline-separated)
            </div>
            <textarea
              className="inp"
              value={form.audience_user_list_text}
              placeholder="u_abc123def456, u_xyz789..."
              onChange={(e) =>
                setField("audience_user_list_text", e.target.value)
              }
              style={{ marginTop: 4, height: 60, fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              STARTS AT
            </div>
            <input
              className="inp"
              type="datetime-local"
              value={form.starts_at_local}
              onChange={(e) => setField("starts_at_local", e.target.value)}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)" }}
            />
          </div>
          <div>
            <div className="mono caps" style={{ fontSize: 9, color: "var(--ink-3)" }}>
              ENDS AT (optional)
            </div>
            <input
              className="inp"
              type="datetime-local"
              value={form.ends_at_local}
              placeholder="∞ leave blank for no end"
              onChange={(e) => setField("ends_at_local", e.target.value)}
              style={{ marginTop: 4, fontFamily: "var(--font-mono)" }}
            />
          </div>
        </div>

        <label
          className="mono"
          style={{
            fontSize: 12,
            color: "var(--ink-2)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <input
            type="checkbox"
            checked={form.dismissable}
            onChange={(e) => setField("dismissable", e.target.checked)}
          />
          dismissable
        </label>

        {error && (
          <div
            className="mono"
            style={{
              fontSize: 12,
              color: "var(--bad)",
              border: "1px solid var(--bad)",
              padding: 8,
            }}
          >
            {error}
          </div>
        )}

        <div
          style={{
            display: "flex",
            gap: 8,
            justifyContent: "flex-end",
            marginTop: 6,
          }}
        >
          {editing && (
            <button type="button" className="btn" onClick={onCancel}>
              Cancel
            </button>
          )}
          <button
            type="submit"
            className="btn primary shadowed"
            disabled={busy}
          >
            {busy ? "Saving..." : editing ? "Save changes" : "Publish"}
          </button>
        </div>

        {audienceList.length > 0 && form.audience_kind !== "all" && (
          <div
            className="mono"
            style={{ fontSize: 10, color: "var(--ink-3)" }}
          >
            audience: [{audienceList.join(", ")}]
          </div>
        )}
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------
// Tab
// ---------------------------------------------------------------------

export default function AnnouncementsTab() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refresh = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await adminAnn.listAnnouncements();
      setItems(data?.items || []);
    } catch (err) {
      setLoadError(err?.message || "Failed to load announcements");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const totalLive = useMemo(
    () => items.filter((i) => i.is_live).length,
    [items],
  );

  const beginEdit = (item) => {
    setEditing(item);
    setError(null);
    setForm({
      title: item.title || "",
      content_kind: item.content_kind,
      content: item.content_kind === "text" ? item.content : "",
      audience_kind: item.audience_kind,
      audience_data:
        item.audience_kind === "tier" ? item.audience_data || [] : [],
      audience_user_list_text:
        item.audience_kind === "user_list"
          ? (item.audience_data || []).join(", ")
          : "",
      starts_at_local: isoForDatetimeLocal(item.starts_at),
      ends_at_local: isoForDatetimeLocal(item.ends_at),
      dismissable: !!item.dismissable,
      priority: item.priority ?? 0,
      cover_file: null,
    });
  };

  const cancelEdit = () => {
    setEditing(null);
    setError(null);
    setForm(EMPTY_FORM);
  };

  const handleDelete = async (item) => {
    if (!window.confirm(`Delete announcement "${item.title || item.id}"?`))
      return;
    try {
      await adminAnn.deleteAnnouncement(item.id);
      if (editing && editing.id === item.id) cancelEdit();
      await refresh();
    } catch (err) {
      window.alert(`Delete failed: ${err?.message || err}`);
    }
  };

  const handleSubmit = async () => {
    setError(null);

    const startsIso = isoFromDatetimeLocal(form.starts_at_local);
    if (!startsIso) {
      setError("starts_at is required");
      return;
    }
    const endsIso = form.ends_at_local
      ? isoFromDatetimeLocal(form.ends_at_local)
      : null;

    let audienceData = null;
    if (form.audience_kind === "tier") {
      if (form.audience_data.length === 0) {
        setError("Select at least one tier");
        return;
      }
      audienceData = form.audience_data;
    } else if (form.audience_kind === "user_list") {
      const list = form.audience_user_list_text
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (list.length === 0) {
        setError("Provide at least one user id");
        return;
      }
      audienceData = list;
    }

    const payload = {
      title: form.title || null,
      content_kind: form.content_kind,
      content:
        form.content_kind === "image" && !editing
          ? "placeholder"
          : form.content,
      audience_kind: form.audience_kind,
      audience_data: audienceData,
      starts_at: startsIso,
      ends_at: endsIso,
      dismissable: form.dismissable,
      priority: form.priority,
    };

    setBusy(true);
    try {
      if (editing) {
        // Patch only the fields the editor touches; content for image
        // announcements is read-only and the backend rejects edits.
        const patch = {
          title: payload.title,
          audience_kind: payload.audience_kind,
          audience_data: payload.audience_data,
          starts_at: payload.starts_at,
          ends_at: payload.ends_at,
          dismissable: payload.dismissable,
          priority: payload.priority,
        };
        if (form.content_kind === "text") {
          patch.content = payload.content;
        }
        await adminAnn.patchAnnouncement(editing.id, patch);
      } else if (form.content_kind === "image") {
        if (!form.cover_file) {
          setError("Image announcements need a cover upload");
          setBusy(false);
          return;
        }
        await adminAnn.createImageAnnouncement(payload, form.cover_file);
      } else {
        await adminAnn.createTextAnnouncement(payload);
      }
      await refresh();
      cancelEdit();
    } catch (err) {
      setError(err?.message || "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: 16,
        }}
      >
        <div>
          <div className="mono caps" style={{ fontSize: 10, color: "var(--ink-3)" }}>
            /api/admin/announcements
          </div>
          <div
            className="display"
            style={{
              fontSize: 32,
              fontWeight: 800,
              letterSpacing: "-0.025em",
              marginTop: 6,
            }}
          >
            Talk to{" "}
            <span style={{ fontStyle: "italic", color: "var(--banana-deep)" }}>
              users.
            </span>
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}
          >
            {loading
              ? "loading..."
              : `${items.length} total · ${totalLive} live`}
          </div>
        </div>
        <button
          className="btn primary shadowed"
          onClick={() => {
            cancelEdit();
            setForm({ ...EMPTY_FORM, starts_at_local: isoForDatetimeLocal(new Date()) });
          }}
        >
          <Icon name="plus" size={13} />
          Compose
        </button>
      </div>

      {loadError && (
        <div
          className="mono"
          style={{
            fontSize: 12,
            color: "var(--bad)",
            border: "1px solid var(--bad)",
            padding: 12,
          }}
        >
          {loadError}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {items.length === 0 && !loading ? (
            <div
              className="mono"
              style={{
                fontSize: 12,
                color: "var(--ink-3)",
                padding: 24,
                border: "1px dashed var(--ink-4)",
                textAlign: "center",
              }}
            >
              no announcements yet — compose one on the right
            </div>
          ) : (
            items.map((item) => (
              <AnnouncementCard
                key={item.id}
                item={item}
                onEdit={beginEdit}
                onDelete={handleDelete}
              />
            ))
          )}
        </div>

        <EditorPanel
          form={form}
          setForm={setForm}
          onSubmit={handleSubmit}
          editing={editing}
          onCancel={cancelEdit}
          busy={busy}
          error={error}
        />
      </div>
    </div>
  );
}
