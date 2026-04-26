"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BananaLogo, Compass, Folder, Image as ImageIcon, Settings, Sparkle, Wand } from "./Icons";

type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  badge?: string;
};

const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: Compass },
  { href: "/create", label: "Create", icon: Wand, badge: "Pro" },
  { href: "/archive", label: "Archive", icon: Folder }
];

const COLLECTIONS = [
  { name: "Editorial", count: 24 },
  { name: "Brand kit", count: 12 },
  { name: "Studio drafts", count: 7 }
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden md:flex md:w-[260px] shrink-0 flex-col gap-6 px-5 py-6 border-r border-white/[0.06] bg-black/30 backdrop-blur-xl">
      <Link href="/dashboard" className="flex items-center gap-2.5 px-1">
        <BananaLogo size={30} />
        <div className="leading-tight">
          <div className="font-display text-[15px] font-semibold tracking-tight text-ink-50">
            Nano Banana
          </div>
          <div className="text-[11px] uppercase tracking-[0.18em] text-banana-400/90">
            Pro Studio
          </div>
        </div>
      </Link>

      <nav className="flex flex-col gap-1">
        {NAV.map(({ href, label, icon: Icon, badge }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={[
                "group flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition",
                active
                  ? "bg-banana-400/10 text-banana-200 border border-banana-400/20"
                  : "text-ink-200 hover:bg-white/[0.04] border border-transparent"
              ].join(" ")}
            >
              <Icon
                size={18}
                className={active ? "text-banana-300" : "text-ink-300 group-hover:text-ink-100"}
              />
              <span className="flex-1">{label}</span>
              {badge ? (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-banana-400/20 text-banana-200 border border-banana-400/20">
                  {badge}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>

      <div className="mt-2">
        <div className="label px-1 mb-2">Collections</div>
        <div className="flex flex-col gap-1">
          {COLLECTIONS.map((c) => (
            <button
              key={c.name}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-ink-200 hover:bg-white/[0.04] transition text-left"
            >
              <span className="h-2 w-2 rounded-full bg-banana-400/80" />
              <span className="flex-1">{c.name}</span>
              <span className="text-xs text-ink-400">{c.count}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="mt-auto card p-4">
        <div className="flex items-center gap-2 text-banana-300">
          <Sparkle size={16} />
          <span className="text-xs font-semibold tracking-wide uppercase">Credits</span>
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="font-display text-2xl font-semibold text-ink-50">847</span>
          <span className="text-xs text-ink-300">/ 1,000</span>
        </div>
        <div className="mt-3 h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
          <div className="h-full w-[84%] bg-gradient-to-r from-banana-400 to-banana-200" />
        </div>
        <button className="btn-primary w-full mt-4 text-xs">Upgrade plan</button>
      </div>

      <Link
        href="#"
        className="flex items-center gap-2 text-xs text-ink-300 hover:text-ink-100 px-1"
      >
        <Settings size={14} /> Settings &amp; preferences
      </Link>
    </aside>
  );
}
