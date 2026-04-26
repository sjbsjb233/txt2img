# Nano Banana Pro — Frontend

Premium text-to-image studio frontend, built with Next.js 14 (App Router), TypeScript, and Tailwind CSS.

## Pages

| Route        | Description                                                                      |
| ------------ | -------------------------------------------------------------------------------- |
| `/login`     | Sign-in screen with email/password and OAuth buttons. Triggers the captcha modal before completing auth. |
| `/dashboard` | Welcome view with stats, hero callout, recent generations and quick prompts.     |
| `/create`    | Studio canvas for text-to-image generation: prompt, style, aspect ratio, advanced controls, 4-up results. |
| `/archive`   | Searchable, filterable masonry gallery of every past generation, with multi-select and a preview dialog. |

A self-contained **Captcha (human verification) modal** lives in `components/CaptchaModal.tsx` and is used both on `/login` and on `/create` (after several rapid generations).

## Local development

```bash
cd frontend
npm install
npm run dev      # http://localhost:3000
```

## Production build

```bash
npm run build
npm run start
```

## Project layout

```
frontend/
├── app/
│   ├── layout.tsx        # root layout, fonts, metadata
│   ├── globals.css       # Tailwind + design tokens
│   ├── page.tsx          # redirects to /login
│   ├── login/page.tsx
│   ├── dashboard/page.tsx
│   ├── create/page.tsx
│   └── archive/page.tsx
├── components/
│   ├── AppShell.tsx      # sidebar + main shell
│   ├── Sidebar.tsx
│   ├── Topbar.tsx
│   ├── CaptchaModal.tsx  # slide-to-verify human check
│   └── Icons.tsx
├── lib/
│   └── mock.ts           # deterministic sample data
├── public/
│   └── favicon.svg
├── tailwind.config.ts
├── tsconfig.json
└── next.config.mjs
```

## Notes

- The captcha is a slide-to-fit puzzle stub. Swap the body of `verify()` in
  `components/CaptchaModal.tsx` for a real provider (Cloudflare Turnstile,
  hCaptcha, etc.) when wiring up to the backend.
- All image previews are CSS gradients keyed off a hue from `lib/mock.ts` so
  the app renders without any external assets.
- Theme tokens (banana yellow + ink) live in `tailwind.config.ts` and
  `app/globals.css`.
