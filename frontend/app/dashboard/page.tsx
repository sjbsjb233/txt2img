import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Topbar } from "@/components/Topbar";
import {
  ArrowRight,
  Folder,
  Heart,
  Image as ImageIcon,
  Sparkle,
  Wand
} from "@/components/Icons";
import { SAMPLE_GENERATIONS, aspectClass, relativeTime, swatch } from "@/lib/mock";

const STATS = [
  { label: "Generations this month", value: "1,284", trend: "+18%", icon: ImageIcon },
  { label: "Credits remaining", value: "847", trend: "Renews May 12", icon: Sparkle },
  { label: "Saved to archive", value: "212", trend: "+24 this week", icon: Folder },
  { label: "Average rating", value: "4.7", trend: "from your reviews", icon: Heart }
];

const QUICK_PROMPTS = [
  "Editorial portrait, soft window light, 35mm grain",
  "Surreal landscape with floating islands at dawn",
  "Minimal product shot on warm beige seamless",
  "Risograph print, two-color, halftone texture"
];

export default function DashboardPage() {
  const recents = SAMPLE_GENERATIONS.slice(0, 6);
  const featured = SAMPLE_GENERATIONS.slice(6, 9);

  return (
    <AppShell>
      <Topbar
        title="Welcome back, Aria"
        subtitle="Pick up where you left off, or start something new."
        cta={{ label: "New creation", href: "/create" }}
      />

      {/* Hero */}
      <section className="card p-6 md:p-8 relative overflow-hidden mb-8">
        <div className="absolute -right-16 -top-16 h-72 w-72 rounded-full bg-banana-400/15 blur-3xl" />
        <div className="absolute -right-32 bottom-0 h-72 w-72 rounded-full bg-banana-300/10 blur-3xl" />
        <div className="relative grid md:grid-cols-[1.2fr_1fr] gap-8 items-center">
          <div>
            <div className="chip mb-3">
              <Sparkle size={12} className="text-banana-300" />
              New · Helios v3.2 model
            </div>
            <h2 className="font-display text-2xl md:text-3xl font-semibold tracking-tight leading-tight">
              Sharper details. Truer color. <span className="text-banana-300">2× faster previews.</span>
            </h2>
            <p className="text-sm text-ink-300 mt-3 max-w-lg">
              Try the new Helios diffusion engine for editorial-grade renders with consistent
              characters and brand palette adherence.
            </p>
            <div className="mt-5 flex flex-wrap gap-2.5">
              <Link href="/create" className="btn-primary">
                <Wand size={16} /> Open studio
              </Link>
              <button className="btn-secondary">
                <ArrowRight size={16} /> What's new
              </button>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            {SAMPLE_GENERATIONS.slice(0, 3).map((g, i) => (
              <div
                key={g.id}
                className={[
                  "rounded-xl border border-white/[0.08] overflow-hidden relative",
                  i === 1 ? "translate-y-3 float-slow" : ""
                ].join(" ")}
                style={{ ...swatch(g.hue), aspectRatio: "3 / 4" }}
              >
                <div className="absolute inset-0 bg-noise opacity-30 mix-blend-overlay" />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Stats */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-10">
        {STATS.map((s) => (
          <div key={s.label} className="card p-4">
            <div className="flex items-center justify-between">
              <span className="label">{s.label}</span>
              <span className="text-banana-300">
                <s.icon size={14} />
              </span>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="font-display text-2xl font-semibold">{s.value}</span>
            </div>
            <div className="text-[11px] text-ink-300 mt-1">{s.trend}</div>
          </div>
        ))}
      </section>

      {/* Recent + Quick prompts */}
      <section className="grid lg:grid-cols-[1.6fr_1fr] gap-6 mb-10">
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-display text-lg font-semibold">Recent generations</h3>
            <Link href="/archive" className="text-xs text-banana-300 hover:text-banana-200 inline-flex items-center gap-1">
              View archive <ArrowRight size={12} />
            </Link>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {recents.map((g) => (
              <article
                key={g.id}
                className="group relative rounded-xl border border-white/[0.06] overflow-hidden"
              >
                <div className={aspectClass(g.ratio)} style={swatch(g.hue)}>
                  <div className="h-full w-full bg-noise opacity-40 mix-blend-overlay" />
                </div>
                <div className="absolute inset-x-0 bottom-0 p-3 bg-gradient-to-t from-black/90 via-black/40 to-transparent opacity-0 group-hover:opacity-100 transition">
                  <p className="text-[11px] text-ink-100 line-clamp-2">{g.prompt}</p>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-300">
                    <span>{g.style}</span>
                    <span>·</span>
                    <span>{relativeTime(g.createdAt)}</span>
                  </div>
                </div>
                <div className="absolute top-2 left-2 chip">{g.ratio}</div>
              </article>
            ))}
          </div>
        </div>

        <div className="space-y-6">
          <div className="card p-5">
            <h3 className="font-display text-base font-semibold mb-1">Jumpstart a prompt</h3>
            <p className="text-xs text-ink-300 mb-4">
              Curated starting points based on your recent style.
            </p>
            <div className="flex flex-col gap-2">
              {QUICK_PROMPTS.map((p) => (
                <Link
                  key={p}
                  href={`/create?prompt=${encodeURIComponent(p)}`}
                  className="group text-left text-sm rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2.5 hover:border-banana-400/40 hover:bg-banana-400/5 transition"
                >
                  <div className="flex items-start gap-2">
                    <Sparkle size={14} className="text-banana-300 mt-0.5 shrink-0" />
                    <span className="text-ink-100 leading-snug">{p}</span>
                  </div>
                </Link>
              ))}
            </div>
          </div>

          <div className="card p-5">
            <h3 className="font-display text-base font-semibold mb-3">Featured collection</h3>
            <div className="grid grid-cols-3 gap-2">
              {featured.map((g) => (
                <div
                  key={g.id}
                  className="aspect-square rounded-lg overflow-hidden border border-white/[0.06]"
                  style={swatch(g.hue)}
                />
              ))}
            </div>
            <p className="text-xs text-ink-300 mt-3">
              "Editorial Spring" · 18 pieces · curated by <span className="text-banana-300">@studio</span>
            </p>
          </div>
        </div>
      </section>
    </AppShell>
  );
}
