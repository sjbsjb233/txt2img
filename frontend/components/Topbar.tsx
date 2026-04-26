"use client";

import Link from "next/link";
import { Bell, Plus, Search } from "./Icons";

type Props = {
  title?: string;
  subtitle?: string;
  searchPlaceholder?: string;
  cta?: { label: string; href: string };
};

export function Topbar({ title, subtitle, searchPlaceholder = "Search prompts, archives, styles…", cta }: Props) {
  return (
    <header className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mb-8">
      <div>
        {title ? (
          <h1 className="font-display text-2xl md:text-3xl font-semibold tracking-tight text-ink-50">
            {title}
          </h1>
        ) : null}
        {subtitle ? <p className="text-sm text-ink-300 mt-1.5">{subtitle}</p> : null}
      </div>

      <div className="flex items-center gap-3">
        <div className="relative w-full md:w-[340px]">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300"
          />
          <input
            className="input pl-9 py-2.5 text-sm"
            placeholder={searchPlaceholder}
          />
          <span className="hidden md:inline absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-ink-300 border border-white/[0.08] rounded px-1.5 py-0.5">
            ⌘K
          </span>
        </div>

        <button className="btn-secondary !p-2.5" aria-label="Notifications">
          <Bell size={16} />
        </button>

        {cta ? (
          <Link href={cta.href} className="btn-primary">
            <Plus size={16} /> {cta.label}
          </Link>
        ) : null}

        <div className="h-9 w-9 rounded-full bg-gradient-to-br from-banana-300 to-banana-500 flex items-center justify-center text-ink-900 font-semibold text-sm border border-white/10">
          AY
        </div>
      </div>
    </header>
  );
}
