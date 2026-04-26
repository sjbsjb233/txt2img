"use client";

import * as React from "react";
import { Check, Close, RefreshCw, Shield } from "./Icons";

type Status = "idle" | "dragging" | "verifying" | "success" | "error";

type Props = {
  open: boolean;
  onClose: () => void;
  onVerified?: () => void;
  title?: string;
  description?: string;
};

/**
 * A self-contained "slide to verify" human verification modal.
 * Replace the slider logic with a real provider (hCaptcha, Cloudflare Turnstile, etc.)
 * by swapping the body of `verify`.
 */
export function CaptchaModal({
  open,
  onClose,
  onVerified,
  title = "Quick human check",
  description = "Slide the puzzle piece to fit the gap. This helps us keep bots out."
}: Props) {
  const trackRef = React.useRef<HTMLDivElement>(null);
  const [target, setTarget] = React.useState<number>(72); // % position the slider must reach
  const [progress, setProgress] = React.useState(0);
  const [status, setStatus] = React.useState<Status>("idle");
  const startX = React.useRef(0);
  const startProgress = React.useRef(0);

  // Reset whenever modal opens
  React.useEffect(() => {
    if (open) {
      reset();
    }
  }, [open]);

  function reset() {
    setProgress(0);
    setStatus("idle");
    setTarget(50 + Math.floor(Math.random() * 35)); // 50..85
  }

  function clamp(n: number) {
    return Math.max(0, Math.min(100, n));
  }

  function onPointerDown(e: React.PointerEvent) {
    if (status === "verifying" || status === "success") return;
    (e.target as Element).setPointerCapture(e.pointerId);
    startX.current = e.clientX;
    startProgress.current = progress;
    setStatus("dragging");
  }

  function onPointerMove(e: React.PointerEvent) {
    if (status !== "dragging") return;
    const track = trackRef.current;
    if (!track) return;
    const width = track.clientWidth;
    const dx = e.clientX - startX.current;
    const next = clamp(startProgress.current + (dx / width) * 100);
    setProgress(next);
  }

  function onPointerUp() {
    if (status !== "dragging") return;
    const tolerance = 5;
    if (Math.abs(progress - target) <= tolerance) {
      verify();
    } else {
      setStatus("error");
      // brief shake then reset
      setTimeout(() => {
        setProgress(0);
        setStatus("idle");
      }, 700);
    }
  }

  function verify() {
    setStatus("verifying");
    // Simulate server-side verification round-trip
    setTimeout(() => {
      setStatus("success");
      setTimeout(() => {
        onVerified?.();
      }, 600);
    }, 800);
  }

  // Close on Escape
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  const pieceLeft = `calc(${progress}% - 22px)`;
  const gapLeft = `calc(${target}% - 22px)`;
  const fillStyle = { width: `${progress}%` };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="captcha-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      {/* backdrop */}
      <button
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/70 backdrop-blur-md"
      />

      <div
        className={[
          "relative w-full max-w-[440px] rounded-2xl glass-strong shadow-soft p-6",
          status === "error" ? "animate-[shake_0.4s_ease-in-out]" : ""
        ].join(" ")}
      >
        <button
          onClick={onClose}
          className="absolute right-3 top-3 p-2 rounded-lg text-ink-300 hover:text-ink-50 hover:bg-white/[0.05]"
          aria-label="Close"
        >
          <Close size={16} />
        </button>

        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-banana-400/15 border border-banana-400/30 flex items-center justify-center text-banana-300">
            <Shield size={20} />
          </div>
          <div>
            <h2 id="captcha-title" className="font-display text-lg font-semibold text-ink-50">
              {title}
            </h2>
            <p className="text-xs text-ink-300">{description}</p>
          </div>
        </div>

        {/* puzzle preview */}
        <div className="relative mt-5 h-[160px] rounded-xl overflow-hidden border border-white/[0.06]">
          <div
            className="absolute inset-0"
            style={{
              backgroundImage:
                "linear-gradient(135deg, rgba(255,214,51,0.18), rgba(34,34,29,0.6) 40%, rgba(12,12,10,1) 100%), radial-gradient(600px 200px at 30% 20%, rgba(255,255,255,0.08), transparent 50%)"
            }}
          />
          <svg
            className="absolute inset-0 w-full h-full opacity-40"
            xmlns="http://www.w3.org/2000/svg"
          >
            <defs>
              <pattern id="grid" width="22" height="22" patternUnits="userSpaceOnUse">
                <path d="M22 0H0V22" stroke="rgba(255,255,255,0.06)" fill="none" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#grid)" />
          </svg>

          {/* gap (target) */}
          <div
            className="absolute top-1/2 -translate-y-1/2 h-11 w-11 rounded-lg border border-dashed border-white/30 bg-black/40"
            style={{ left: gapLeft }}
            aria-hidden
          />

          {/* slider piece preview */}
          <div
            className={[
              "absolute top-1/2 -translate-y-1/2 h-11 w-11 rounded-lg shadow-glow border border-banana-300/60",
              status === "success"
                ? "bg-emerald-400/90"
                : status === "error"
                ? "bg-red-400/80"
                : "bg-banana-400"
            ].join(" ")}
            style={{ left: pieceLeft }}
            aria-hidden
          />

          {/* status overlay */}
          {status === "verifying" && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/40">
              <div className="flex items-center gap-2 text-sm text-ink-100">
                <span className="h-4 w-4 rounded-full border-2 border-banana-300 border-t-transparent animate-spin" />
                Verifying…
              </div>
            </div>
          )}
          {status === "success" && (
            <div className="absolute inset-0 flex items-center justify-center bg-emerald-500/15">
              <div className="flex items-center gap-2 text-sm text-emerald-300 font-medium">
                <Check size={16} /> Verified
              </div>
            </div>
          )}
        </div>

        {/* slider */}
        <div className="mt-5 select-none">
          <div
            ref={trackRef}
            className="relative h-12 rounded-xl bg-black/40 border border-white/[0.08] overflow-hidden"
          >
            <div
              className={[
                "absolute inset-y-0 left-0",
                status === "success"
                  ? "bg-emerald-400/30"
                  : status === "error"
                  ? "bg-red-400/30"
                  : "bg-banana-400/25"
              ].join(" ")}
              style={fillStyle}
            />
            <div className="absolute inset-0 flex items-center justify-center text-xs text-ink-300 pointer-events-none">
              {status === "success"
                ? "You're verified. Continuing…"
                : status === "error"
                ? "Almost — try again"
                : "Slide right to fit the puzzle piece"}
            </div>
            <div
              role="slider"
              tabIndex={0}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress)}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              className={[
                "absolute top-1 bottom-1 w-12 rounded-lg cursor-grab active:cursor-grabbing touch-none",
                "flex items-center justify-center transition-colors",
                status === "success"
                  ? "bg-emerald-400 text-emerald-950"
                  : status === "error"
                  ? "bg-red-400 text-red-950"
                  : "bg-banana-400 text-ink-900 shadow-glow"
              ].join(" ")}
              style={{ left: `calc(${progress}% - 24px + 4px)` }}
            >
              {status === "success" ? (
                <Check size={18} />
              ) : status === "verifying" ? (
                <span className="h-3 w-3 rounded-full border-2 border-ink-900 border-t-transparent animate-spin" />
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M5 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" opacity="0.4" />
                </svg>
              )}
            </div>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-ink-300">
          <button
            onClick={reset}
            className="inline-flex items-center gap-1.5 hover:text-ink-100 transition"
          >
            <RefreshCw size={14} /> Try a new puzzle
          </button>
          <span className="opacity-70">Protected by Nano Banana Sentinel</span>
        </div>
      </div>

      <style jsx>{`
        @keyframes shake {
          0%, 100% { transform: translateX(0); }
          20% { transform: translateX(-6px); }
          40% { transform: translateX(6px); }
          60% { transform: translateX(-4px); }
          80% { transform: translateX(4px); }
        }
      `}</style>
    </div>
  );
}
