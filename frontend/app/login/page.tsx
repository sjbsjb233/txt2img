"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { CaptchaModal } from "@/components/CaptchaModal";
import {
  ArrowRight,
  BananaLogo,
  Eye,
  EyeOff,
  Github,
  Google,
  Lock,
  Mail,
  Sparkle
} from "@/components/Icons";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [remember, setRemember] = React.useState(true);
  const [captchaOpen, setCaptchaOpen] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email || !password) return;
    setCaptchaOpen(true);
  }

  function onVerified() {
    setCaptchaOpen(false);
    setSubmitting(true);
    // Simulate auth
    setTimeout(() => router.push("/dashboard"), 500);
  }

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      {/* Left visual panel */}
      <section className="relative hidden lg:flex flex-col justify-between p-10 overflow-hidden border-r border-white/[0.06]">
        <div className="absolute inset-0 bg-banana-radial" />
        <div className="absolute inset-0 bg-noise opacity-40 mix-blend-overlay" />
        <div className="absolute -right-20 top-1/4 h-[420px] w-[420px] rounded-full bg-banana-400/20 blur-[120px]" />
        <div className="absolute -left-20 bottom-0 h-[360px] w-[360px] rounded-full bg-banana-300/10 blur-[120px]" />

        <Link href="/" className="relative z-10 flex items-center gap-2.5">
          <BananaLogo size={32} />
          <div className="leading-tight">
            <div className="font-display text-base font-semibold">Nano Banana</div>
            <div className="text-[11px] uppercase tracking-[0.2em] text-banana-300">Pro Studio</div>
          </div>
        </Link>

        <div className="relative z-10 max-w-md">
          <div className="chip mb-5">
            <Sparkle size={12} className="text-banana-300" />
            v3.2 · Helios diffusion model
          </div>
          <h1 className="font-display text-4xl xl:text-5xl font-semibold tracking-tight leading-[1.05]">
            Compose worlds <br />
            from a single sentence.
          </h1>
          <p className="mt-5 text-ink-200 text-sm xl:text-base leading-relaxed max-w-sm">
            A studio-grade text-to-image workspace built for creative directors, brand teams, and
            independent artists. Iterate fast, archive everything, ship beautifully.
          </p>

          <div className="mt-8 grid grid-cols-2 gap-3 max-w-sm">
            {[
              { k: "8K", v: "ultra-res output" },
              { k: "32 styles", v: "studio presets" },
              { k: "<6s", v: "first preview" },
              { k: "100%", v: "private archive" }
            ].map((s) => (
              <div key={s.k} className="card p-3.5">
                <div className="font-display text-lg font-semibold text-banana-200">{s.k}</div>
                <div className="text-xs text-ink-300">{s.v}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative z-10 flex items-center gap-3 text-xs text-ink-300">
          <div className="flex -space-x-2">
            {["#FFD633", "#F6C000", "#FFE566"].map((c, i) => (
              <div
                key={i}
                className="h-7 w-7 rounded-full border border-ink-900"
                style={{ background: c }}
              />
            ))}
          </div>
          Trusted by 24,000+ creators across 60 countries.
        </div>
      </section>

      {/* Right form panel */}
      <section className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-[420px]">
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <BananaLogo size={28} />
            <div className="font-display text-base font-semibold">Nano Banana Pro</div>
          </div>

          <h2 className="font-display text-2xl font-semibold tracking-tight">Welcome back</h2>
          <p className="text-sm text-ink-300 mt-1.5">
            Sign in to continue your creative session.
          </p>

          <div className="mt-6 grid grid-cols-2 gap-2.5">
            <button className="btn-secondary">
              <Google size={16} /> Google
            </button>
            <button className="btn-secondary">
              <Github size={16} /> GitHub
            </button>
          </div>

          <div className="my-6 flex items-center gap-3 text-[11px] uppercase tracking-[0.2em] text-ink-400">
            <div className="flex-1 h-px bg-white/[0.08]" />
            or with email
            <div className="flex-1 h-px bg-white/[0.08]" />
          </div>

          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <label className="label block mb-1.5">Email</label>
              <div className="relative">
                <Mail
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300"
                />
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@studio.com"
                  className="input pl-9"
                />
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="label">Password</label>
                <Link href="#" className="text-xs text-banana-300 hover:text-banana-200">
                  Forgot password?
                </Link>
              </div>
              <div className="relative">
                <Lock
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300"
                />
                <input
                  type={showPassword ? "text" : "password"}
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="input pl-9 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-300 hover:text-ink-100"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs text-ink-200 select-none">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="h-4 w-4 rounded border-white/20 bg-black/40 accent-banana-400"
              />
              Keep me signed in for 30 days
            </label>

            <button type="submit" disabled={submitting} className="btn-primary w-full">
              {submitting ? (
                <span className="flex items-center gap-2">
                  <span className="h-3.5 w-3.5 rounded-full border-2 border-ink-900 border-t-transparent animate-spin" />
                  Signing in…
                </span>
              ) : (
                <>
                  Sign in <ArrowRight size={16} />
                </>
              )}
            </button>
          </form>

          <p className="text-xs text-ink-300 text-center mt-6">
            New to Nano Banana?{" "}
            <Link href="#" className="text-banana-300 hover:text-banana-200">
              Request access
            </Link>
          </p>

          <p className="text-[10px] text-ink-400 text-center mt-8 leading-relaxed">
            By continuing, you agree to our{" "}
            <Link href="#" className="underline underline-offset-2">
              Terms
            </Link>{" "}
            and{" "}
            <Link href="#" className="underline underline-offset-2">
              Privacy Policy
            </Link>
            . Protected by Nano Banana Sentinel.
          </p>
        </div>
      </section>

      <CaptchaModal
        open={captchaOpen}
        onClose={() => setCaptchaOpen(false)}
        onVerified={onVerified}
        title="Confirm you're human"
        description="Quick security check before we sign you in."
      />
    </div>
  );
}
