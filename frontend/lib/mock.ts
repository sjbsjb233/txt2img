// Shared mock data + helpers used across pages.
import type { CSSProperties } from "react";

export type Generation = {
  id: string;
  prompt: string;
  style: string;
  ratio: "1:1" | "3:4" | "4:3" | "16:9" | "9:16";
  createdAt: string; // ISO
  steps: number;
  seed: number;
  liked?: boolean;
  collection?: string;
  // Procedural placeholder hue so rendered swatches look distinct without external images.
  hue: number;
};

const STYLES = [
  "Editorial film",
  "Studio product",
  "Risograph print",
  "Painterly oil",
  "Cyberpunk neon",
  "Soft 3D clay",
  "Brutalist photo",
  "Magical realism",
  "Botanical sketch",
  "Mid-century poster"
];

const PROMPTS = [
  "A miniature library inside a glass jar, golden afternoon light, dust motes",
  "Wide-angle photo of a banana yellow vintage convertible on a salt flat at dusk",
  "Studio shot of a translucent ceramic vase, key light, soft seamless background",
  "Hand-painted travel poster of Lisbon, art-deco geometry, muted ochres",
  "Macro of a dewdrop on a marigold petal, shallow depth of field, morning",
  "Risograph print of a fox reading a newspaper, two-color print, grain",
  "Aerial of a coastal town at golden hour, long shadows, gentle haze",
  "A neon-soaked night market in Tokyo, rain reflections, cinematic",
  "Brutalist concrete chapel, stark light, monochrome, shot on medium format",
  "A single sunflower against deep cobalt sky, oil painting, impasto",
  "Botanical illustration of a dragon fruit, ink and watercolor on cream paper",
  "A clay-stop-motion crab playing chess with a snail, soft three-point lighting"
];

function rand(seed: number) {
  // Deterministic pseudo-random for stable SSR/CSR
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

export const SAMPLE_GENERATIONS: Generation[] = Array.from({ length: 24 }).map((_, i) => {
  const seed = 1000 + i * 7;
  return {
    id: `gen_${seed}`,
    prompt: PROMPTS[i % PROMPTS.length],
    style: STYLES[i % STYLES.length],
    ratio: (["1:1", "3:4", "4:3", "16:9", "9:16"] as const)[i % 5],
    createdAt: new Date(Date.now() - i * 1000 * 60 * 60 * 7).toISOString(),
    steps: 24 + (i % 5) * 4,
    seed,
    liked: i % 4 === 0,
    collection: ["Editorial", "Brand kit", "Studio drafts", undefined][i % 4],
    hue: Math.floor(rand(seed) * 360)
  };
});

export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function aspectClass(r: Generation["ratio"]) {
  switch (r) {
    case "1:1":
      return "aspect-square";
    case "3:4":
      return "aspect-[3/4]";
    case "4:3":
      return "aspect-[4/3]";
    case "16:9":
      return "aspect-[16/9]";
    case "9:16":
      return "aspect-[9/16]";
  }
}

/** Generate a CSS gradient swatch from a hue, used as a placeholder for art. */
export function swatch(hue: number): CSSProperties {
  return {
    background: `linear-gradient(135deg, hsl(${hue} 80% 60% / 0.85), hsl(${
      (hue + 40) % 360
    } 70% 40% / 0.85) 60%, hsl(${(hue + 80) % 360} 60% 20% / 0.95))`
  };
}
