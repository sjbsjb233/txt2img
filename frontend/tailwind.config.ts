import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"]
      },
      colors: {
        banana: {
          50: "#fffdf2",
          100: "#fff9d6",
          200: "#fff1a3",
          300: "#ffe566",
          400: "#ffd633",
          500: "#f6c000",
          600: "#cc9a00",
          700: "#8a6500",
          800: "#5e4400",
          900: "#3a2a00"
        },
        ink: {
          50: "#f5f5f4",
          100: "#e7e7e3",
          200: "#c8c8c1",
          300: "#9c9c91",
          400: "#6f6f64",
          500: "#4a4a40",
          600: "#33332b",
          700: "#22221d",
          800: "#16161310",
          900: "#0c0c0a"
        }
      },
      backgroundImage: {
        "banana-radial":
          "radial-gradient(1200px 600px at 80% -10%, rgba(255,214,51,0.18), transparent 60%), radial-gradient(900px 500px at -10% 100%, rgba(246,192,0,0.10), transparent 60%)",
        "noise":
          "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.06 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>\")"
      },
      boxShadow: {
        soft: "0 1px 0 rgba(255,255,255,0.04) inset, 0 10px 30px rgba(0,0,0,0.35)",
        glow: "0 0 0 1px rgba(255,214,51,0.35), 0 10px 40px rgba(255,214,51,0.18)"
      },
      borderRadius: {
        xl2: "1.25rem"
      }
    }
  },
  plugins: []
};

export default config;
