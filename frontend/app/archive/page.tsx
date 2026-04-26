"use client";

import * as React from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Topbar } from "@/components/Topbar";
import {
  Download,
  Filter,
  Heart,
  Plus,
  Search,
  Trash
} from "@/components/Icons";
import { SAMPLE_GENERATIONS, aspectClass, relativeTime, swatch, type Generation } from "@/lib/mock";

const COLLECTIONS = ["All", "Editorial", "Brand kit", "Studio drafts", "Liked"];
const RATIOS = ["All", "1:1", "3:4", "4:3", "16:9", "9:16"] as const;
const SORTS = ["Newest", "Oldest", "Most liked"] as const;

type Sort = (typeof SORTS)[number];

export default function ArchivePage() {
  const [query, setQuery] = React.useState("");
  const [collection, setCollection] = React.useState<string>("All");
  const [ratio, setRatio] = React.useState<(typeof RATIOS)[number]>("All");
  const [sort, setSort] = React.useState<Sort>("Newest");
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [preview, setPreview] = React.useState<Generation | null>(null);

  const filtered = React.useMemo(() => {
    let list = [...SAMPLE_GENERATIONS];
    if (query.trim()) {
      const q = query.toLowerCase();
      list = list.filter(
        (g) => g.prompt.toLowerCase().includes(q) || g.style.toLowerCase().includes(q)
      );
    }
    if (collection !== "All") {
      if (collection === "Liked") list = list.filter((g) => g.liked);
      else list = list.filter((g) => g.collection === collection);
    }
    if (ratio !== "All") list = list.filter((g) => g.ratio === ratio);

    if (sort === "Newest")
      list.sort((a, b) => +new Date(b.createdAt) - +new Date(a.createdAt));
    if (sort === "Oldest")
      list.sort((a, b) => +new Date(a.createdAt) - +new Date(b.createdAt));
    if (sort === "Most liked") list.sort((a, b) => Number(b.liked) - Number(a.liked));
    return list;
  }, [query, collection, ratio, sort]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearSelection() {
    setSelected(new Set());
  }

  return (
    <AppShell>
      <Topbar
        title="Archive"
        subtitle={`${SAMPLE_GENERATIONS.length} pieces · everything you've ever generated`}
        cta={{ label: "New creation", href: "/create" }}
      />

      {/* Filters */}
      <div className="card p-3 mb-6 flex flex-col lg:flex-row gap-3 lg:items-center">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by prompt, style, or seed…"
            className="input pl-9 py-2.5"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <FilterGroup
            label="Collection"
            options={COLLECTIONS}
            value={collection}
            onChange={setCollection}
          />
          <FilterGroup
            label="Ratio"
            options={RATIOS as unknown as string[]}
            value={ratio}
            onChange={(v) => setRatio(v as (typeof RATIOS)[number])}
          />
          <div className="relative">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              className="input !py-2 !pr-8 text-sm cursor-pointer"
            >
              {SORTS.map((s) => (
                <option key={s} value={s} className="bg-ink-900">
                  {s}
                </option>
              ))}
            </select>
          </div>
          <button className="btn-secondary !py-2" aria-label="More filters">
            <Filter size={14} />
          </button>
        </div>
      </div>

      {/* Selection bar */}
      {selected.size > 0 && (
        <div className="mb-4 flex items-center justify-between rounded-xl border border-banana-400/30 bg-banana-400/10 px-4 py-2.5">
          <span className="text-sm text-banana-100">
            {selected.size} selected
          </span>
          <div className="flex items-center gap-2">
            <button className="btn-secondary !py-1.5 text-xs">
              <Plus size={14} /> Add to collection
            </button>
            <button className="btn-secondary !py-1.5 text-xs">
              <Download size={14} /> Download
            </button>
            <button className="btn-secondary !py-1.5 text-xs text-red-300 hover:text-red-200">
              <Trash size={14} /> Delete
            </button>
            <button onClick={clearSelection} className="btn-ghost !py-1.5 text-xs">
              Clear
            </button>
          </div>
        </div>
      )}

      {/* Grid */}
      {filtered.length === 0 ? (
        <div className="card p-10 text-center">
          <p className="text-ink-200 font-medium">No matching pieces</p>
          <p className="text-xs text-ink-400 mt-1">Try clearing filters or adjusting your search.</p>
        </div>
      ) : (
        <div className="columns-2 md:columns-3 xl:columns-4 gap-3 [column-fill:_balance]">
          {filtered.map((g) => {
            const isSelected = selected.has(g.id);
            return (
              <article
                key={g.id}
                onClick={() => setPreview(g)}
                className={[
                  "group relative mb-3 break-inside-avoid rounded-xl border overflow-hidden cursor-zoom-in transition",
                  isSelected
                    ? "border-banana-400/70 ring-2 ring-banana-400/40"
                    : "border-white/[0.06] hover:border-white/20"
                ].join(" ")}
              >
                <div className={aspectClass(g.ratio)} style={swatch(g.hue)}>
                  <div className="h-full w-full bg-noise opacity-40 mix-blend-overlay" />
                </div>

                {/* checkbox */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    toggle(g.id);
                  }}
                  className={[
                    "absolute top-2 left-2 h-5 w-5 rounded-md border flex items-center justify-center transition",
                    isSelected
                      ? "bg-banana-400 border-banana-400 text-ink-900"
                      : "bg-black/40 border-white/30 opacity-0 group-hover:opacity-100"
                  ].join(" ")}
                  aria-label={isSelected ? "Deselect" : "Select"}
                >
                  {isSelected ? (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                      <path d="M5 12l4 4L19 6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : null}
                </button>

                {g.liked ? (
                  <span className="absolute top-2 right-2 chip text-banana-200 border-banana-400/40 bg-banana-400/15">
                    <Heart size={10} /> Liked
                  </span>
                ) : null}

                <div className="absolute inset-x-0 bottom-0 p-3 bg-gradient-to-t from-black/85 via-black/30 to-transparent opacity-0 group-hover:opacity-100 transition">
                  <p className="text-[11px] text-ink-100 line-clamp-2">{g.prompt}</p>
                  <div className="mt-1 flex items-center gap-1.5 text-[10px] text-ink-300">
                    <span>{g.style}</span>
                    <span>·</span>
                    <span>{g.ratio}</span>
                    <span>·</span>
                    <span>{relativeTime(g.createdAt)}</span>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {/* Preview dialog */}
      {preview && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-40 flex items-center justify-center p-4"
        >
          <button
            aria-label="Close preview"
            onClick={() => setPreview(null)}
            className="absolute inset-0 bg-black/80 backdrop-blur-md"
          />
          <div className="relative grid lg:grid-cols-[1.6fr_1fr] gap-0 max-w-[1100px] w-full max-h-[88vh] rounded-2xl overflow-hidden glass-strong shadow-soft">
            <div
              className="min-h-[320px] lg:min-h-[600px] relative"
              style={swatch(preview.hue)}
            >
              <div className="absolute inset-0 bg-noise opacity-30 mix-blend-overlay" />
            </div>
            <div className="p-6 flex flex-col gap-4 bg-ink-900/80">
              <div className="flex items-start justify-between">
                <div>
                  <div className="label">Prompt</div>
                  <p className="mt-1.5 text-sm text-ink-100 leading-relaxed">{preview.prompt}</p>
                </div>
                <button
                  onClick={() => setPreview(null)}
                  className="btn-ghost !p-2"
                  aria-label="Close"
                >
                  ×
                </button>
              </div>

              <dl className="grid grid-cols-2 gap-3 text-sm">
                <Detail label="Style" value={preview.style} />
                <Detail label="Aspect" value={preview.ratio} />
                <Detail label="Steps" value={String(preview.steps)} />
                <Detail label="Seed" value={String(preview.seed)} />
                <Detail label="Created" value={relativeTime(preview.createdAt)} />
                <Detail label="Collection" value={preview.collection ?? "—"} />
              </dl>

              <div className="mt-auto flex flex-wrap gap-2 pt-4 border-t border-white/[0.06]">
                <button className="btn-primary text-xs">
                  <Download size={14} /> Download
                </button>
                <Link
                  href={`/create?prompt=${encodeURIComponent(preview.prompt)}`}
                  className="btn-secondary text-xs"
                >
                  Remix
                </Link>
                <button className="btn-secondary text-xs">
                  <Heart size={14} /> {preview.liked ? "Liked" : "Like"}
                </button>
                <button className="btn-secondary text-xs text-red-300 hover:text-red-200">
                  <Trash size={14} /> Delete
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function FilterGroup({
  label,
  options,
  value,
  onChange
}: {
  label: string;
  options: readonly string[] | string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-1 rounded-xl bg-black/30 border border-white/[0.06] p-1">
      <span className="text-[10px] uppercase tracking-[0.14em] text-ink-400 px-2">{label}</span>
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={[
            "px-2.5 py-1 rounded-lg text-xs transition",
            value === o
              ? "bg-banana-400/20 text-banana-200 border border-banana-400/30"
              : "text-ink-200 hover:bg-white/[0.04] border border-transparent"
          ].join(" ")}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="text-sm text-ink-100 mt-0.5">{value}</div>
    </div>
  );
}
