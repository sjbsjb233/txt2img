"use client";

import * as React from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Topbar } from "@/components/Topbar";
import { CaptchaModal } from "@/components/CaptchaModal";
import {
  Download,
  Heart,
  RefreshCw,
  Shield,
  Sparkle,
  Wand
} from "@/components/Icons";
import { SAMPLE_GENERATIONS, swatch } from "@/lib/mock";

const STYLES = [
  "Editorial film",
  "Studio product",
  "Risograph",
  "Painterly",
  "Cyberpunk",
  "Soft 3D",
  "Brutalist photo",
  "Botanical"
];

const RATIOS: { label: string; value: "1:1" | "3:4" | "4:3" | "16:9" | "9:16" }[] = [
  { label: "Square", value: "1:1" },
  { label: "Portrait", value: "3:4" },
  { label: "Landscape", value: "4:3" },
  { label: "Wide", value: "16:9" },
  { label: "Story", value: "9:16" }
];

const QUALITY = [
  { label: "Draft", desc: "Fastest · 2 credits", value: "draft" },
  { label: "Standard", desc: "Balanced · 4 credits", value: "standard" },
  { label: "Pro", desc: "Highest detail · 8 credits", value: "pro" }
] as const;

type RatioVal = (typeof RATIOS)[number]["value"];
type QualityVal = (typeof QUALITY)[number]["value"];

export default function CreatePage() {
  const [prompt, setPrompt] = React.useState(
    "An editorial portrait of a woman in a banana yellow trench coat, soft window light, 35mm grain"
  );
  const [negative, setNegative] = React.useState("text, watermark, low contrast");
  const [style, setStyle] = React.useState(STYLES[0]);
  const [ratio, setRatio] = React.useState<RatioVal>("3:4");
  const [quality, setQuality] = React.useState<QualityVal>("standard");
  const [steps, setSteps] = React.useState(28);
  const [guidance, setGuidance] = React.useState(7.5);
  const [seed, setSeed] = React.useState<number | "">("");
  const [generating, setGenerating] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const [results, setResults] = React.useState(SAMPLE_GENERATIONS.slice(0, 4));
  const [captchaOpen, setCaptchaOpen] = React.useState(false);
  const generationsTodayRef = React.useRef(0);

  function startGeneration() {
    // Trigger captcha every 3 generations within session
    if (generationsTodayRef.current > 0 && generationsTodayRef.current % 3 === 0) {
      setCaptchaOpen(true);
      return;
    }
    runGeneration();
  }

  function runGeneration() {
    setGenerating(true);
    setProgress(0);
    const start = Date.now();
    const duration = 2400;
    const tick = () => {
      const p = Math.min(100, ((Date.now() - start) / duration) * 100);
      setProgress(p);
      if (p < 100) {
        requestAnimationFrame(tick);
      } else {
        // build 4 new results from the prompt
        const baseSeed = typeof seed === "number" ? seed : Math.floor(Math.random() * 99999);
        const next = Array.from({ length: 4 }).map((_, i) => ({
          id: `gen_${baseSeed}_${i}`,
          prompt,
          style,
          ratio,
          createdAt: new Date().toISOString(),
          steps,
          seed: baseSeed + i,
          liked: false,
          collection: undefined,
          hue: (baseSeed + i * 47) % 360
        }));
        setResults(next);
        setGenerating(false);
        generationsTodayRef.current += 1;
      }
    };
    requestAnimationFrame(tick);
  }

  function aspectStyle(): React.CSSProperties {
    const map: Record<RatioVal, string> = {
      "1:1": "1 / 1",
      "3:4": "3 / 4",
      "4:3": "4 / 3",
      "16:9": "16 / 9",
      "9:16": "9 / 16"
    };
    return { aspectRatio: map[ratio] };
  }

  return (
    <AppShell>
      <Topbar
        title="Create"
        subtitle="Compose a prompt, dial the look, generate four variations."
      />

      <div className="grid lg:grid-cols-[420px_1fr] gap-6">
        {/* Left: Prompt panel */}
        <aside className="card p-5 space-y-5 self-start lg:sticky lg:top-6">
          <div>
            <label className="label block mb-2">Prompt</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={5}
              className="input resize-none"
              placeholder="Describe what you want to see…"
            />
            <div className="flex items-center justify-between mt-2 text-[11px] text-ink-300">
              <span>{prompt.length} / 1,000</span>
              <button className="inline-flex items-center gap-1 hover:text-banana-300">
                <Sparkle size={12} /> Enhance prompt
              </button>
            </div>
          </div>

          <div>
            <label className="label block mb-2">Avoid</label>
            <input
              value={negative}
              onChange={(e) => setNegative(e.target.value)}
              className="input"
              placeholder="text, watermark, …"
            />
          </div>

          <div>
            <label className="label block mb-2">Style preset</label>
            <div className="flex flex-wrap gap-1.5">
              {STYLES.map((s) => (
                <button
                  key={s}
                  onClick={() => setStyle(s)}
                  className={[
                    "px-3 py-1.5 rounded-full text-xs border transition",
                    style === s
                      ? "border-banana-400/60 bg-banana-400/15 text-banana-200"
                      : "border-white/[0.08] bg-white/[0.03] text-ink-200 hover:border-white/20"
                  ].join(" ")}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="label block mb-2">Aspect ratio</label>
            <div className="grid grid-cols-5 gap-1.5">
              {RATIOS.map((r) => (
                <button
                  key={r.value}
                  onClick={() => setRatio(r.value)}
                  title={r.label}
                  className={[
                    "py-2 rounded-lg text-[11px] border transition flex flex-col items-center gap-1",
                    ratio === r.value
                      ? "border-banana-400/60 bg-banana-400/10 text-banana-200"
                      : "border-white/[0.08] bg-white/[0.03] text-ink-200 hover:border-white/20"
                  ].join(" ")}
                >
                  <span
                    className="block bg-current/30 rounded-sm"
                    style={{
                      width: r.value === "9:16" || r.value === "3:4" ? 10 : r.value === "1:1" ? 14 : 18,
                      height: r.value === "9:16" || r.value === "3:4" ? 18 : r.value === "1:1" ? 14 : r.value === "4:3" ? 12 : 10,
                      background: "currentColor",
                      opacity: 0.5
                    }}
                  />
                  {r.value}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="label block mb-2">Quality</label>
            <div className="grid grid-cols-3 gap-1.5">
              {QUALITY.map((q) => (
                <button
                  key={q.value}
                  onClick={() => setQuality(q.value)}
                  className={[
                    "p-3 rounded-xl text-left border transition",
                    quality === q.value
                      ? "border-banana-400/60 bg-banana-400/10"
                      : "border-white/[0.08] bg-white/[0.03] hover:border-white/20"
                  ].join(" ")}
                >
                  <div className="text-sm font-medium text-ink-50">{q.label}</div>
                  <div className="text-[10px] text-ink-300">{q.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <details className="group">
            <summary className="cursor-pointer list-none flex items-center justify-between text-xs uppercase tracking-[0.14em] text-ink-300">
              <span>Advanced</span>
              <span className="transition group-open:rotate-180">▾</span>
            </summary>
            <div className="mt-4 space-y-4">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs text-ink-200">Steps</span>
                  <span className="text-xs text-banana-300 tabular-nums">{steps}</span>
                </div>
                <input
                  type="range"
                  min={10}
                  max={60}
                  value={steps}
                  onChange={(e) => setSteps(Number(e.target.value))}
                  className="w-full accent-banana-400"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs text-ink-200">Guidance</span>
                  <span className="text-xs text-banana-300 tabular-nums">{guidance.toFixed(1)}</span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={15}
                  step={0.1}
                  value={guidance}
                  onChange={(e) => setGuidance(Number(e.target.value))}
                  className="w-full accent-banana-400"
                />
              </div>
              <div>
                <span className="text-xs text-ink-200 block mb-1.5">Seed</span>
                <div className="flex gap-2">
                  <input
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(e.target.value === "" ? "" : Number(e.target.value))}
                    placeholder="Random"
                    className="input"
                  />
                  <button
                    type="button"
                    onClick={() => setSeed(Math.floor(Math.random() * 99999))}
                    className="btn-secondary !px-3"
                    aria-label="Randomize seed"
                  >
                    <RefreshCw size={14} />
                  </button>
                </div>
              </div>
            </div>
          </details>

          <div className="pt-2 border-t border-white/[0.06]">
            <button
              onClick={startGeneration}
              disabled={generating || prompt.trim().length === 0}
              className="btn-primary w-full"
            >
              {generating ? (
                <span className="flex items-center gap-2">
                  <span className="h-3.5 w-3.5 rounded-full border-2 border-ink-900 border-t-transparent animate-spin" />
                  Generating…
                </span>
              ) : (
                <>
                  <Wand size={16} /> Generate · {quality === "draft" ? 2 : quality === "standard" ? 4 : 8} credits
                </>
              )}
            </button>
            <p className="text-[10px] text-ink-400 mt-2 inline-flex items-center gap-1.5">
              <Shield size={11} /> Heavy use may trigger a quick human check.
            </p>
          </div>
        </aside>

        {/* Right: Canvas */}
        <section className="space-y-5 min-w-0">
          {generating && (
            <div className="card p-4">
              <div className="flex items-center justify-between text-xs text-ink-300 mb-2">
                <span className="inline-flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-banana-400 animate-pulse" />
                  Rendering 4 variations · Helios v3.2
                </span>
                <span className="tabular-nums">{Math.round(progress)}%</span>
              </div>
              <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-banana-400 to-banana-200 transition-[width]"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {results.map((g, i) => (
              <article key={g.id} className="card overflow-hidden group">
                <div className="relative" style={aspectStyle()}>
                  <div className="absolute inset-0" style={swatch(g.hue)} />
                  <div className="absolute inset-0 bg-noise opacity-30 mix-blend-overlay" />
                  {generating && (
                    <div className="absolute inset-0 shimmer opacity-60" />
                  )}
                  <div className="absolute top-2 left-2 chip">#{i + 1}</div>
                  <div className="absolute top-2 right-2 chip">{ratio}</div>

                  <div className="absolute inset-x-0 bottom-0 p-3 flex items-center justify-between gap-2 opacity-0 group-hover:opacity-100 transition bg-gradient-to-t from-black/80 to-transparent">
                    <button
                      className="btn-secondary !py-1.5 !px-2.5 text-xs"
                      aria-label="Like"
                    >
                      <Heart size={14} />
                    </button>
                    <button
                      className="btn-secondary !py-1.5 !px-2.5 text-xs"
                      aria-label="Variations"
                    >
                      <RefreshCw size={14} /> Variations
                    </button>
                    <button
                      className="btn-secondary !py-1.5 !px-2.5 text-xs"
                      aria-label="Download"
                    >
                      <Download size={14} />
                    </button>
                  </div>
                </div>
                <div className="p-3 border-t border-white/[0.06]">
                  <p className="text-xs text-ink-200 line-clamp-2">{g.prompt}</p>
                  <div className="text-[11px] text-ink-400 mt-1.5 flex items-center gap-2">
                    <span>{g.style}</span>
                    <span>·</span>
                    <span>seed {g.seed}</span>
                    <span>·</span>
                    <span>{g.steps} steps</span>
                  </div>
                </div>
              </article>
            ))}
          </div>

          <div className="card p-4 flex items-center justify-between">
            <div className="text-xs text-ink-300">
              Press <kbd className="kbd">⌘ Enter</kbd> to regenerate. Edit prompt and styles on the
              left.
            </div>
            <Link href="/archive" className="text-xs text-banana-300 hover:text-banana-200">
              Open archive →
            </Link>
          </div>
        </section>
      </div>

      <CaptchaModal
        open={captchaOpen}
        onClose={() => setCaptchaOpen(false)}
        onVerified={() => {
          setCaptchaOpen(false);
          runGeneration();
        }}
        title="Quick safety check"
        description="You've generated several images in a row. Confirm you're human to continue."
      />

      <style jsx>{`
        .kbd {
          font-family: var(--font-sans);
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 4px;
          background: rgba(255, 255, 255, 0.06);
          border: 1px solid rgba(255, 255, 255, 0.12);
        }
      `}</style>
    </AppShell>
  );
}
