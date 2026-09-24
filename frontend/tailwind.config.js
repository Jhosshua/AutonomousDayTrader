/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./hooks/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        obsidian: {
          950: "#000000",
          900: "#0a0a0c",
          800: "#121218",
          700: "#181822",
          600: "#222230",
        },
        apple: {
          green: "#30d158",
          red: "#ff453a",
          blue: "#0a84ff",
          purple: "#5e5ce6",
          orange: "#ff9f0a",
          teal: "#64d2ff",
        },
        // Plain-language redesign (2026-09-24), light theme "muted palette" only.
        ground: "#F7F3EC",
        ink: "#1D1A33",
        muted: "#5D5A73",
        line: "#EFE4D2",
        darkcard: "#2E3244",
        gain: "#2F6B4C",
        gainbg: "#E4EFE7",
        loss: "#8F4424",
        lossbg: "#F6E3DA",
        lavender: "#8189C4",
        sage: "#5E9A7A",
        terracotta: "#A9553A",
      },
      fontFamily: {
        // F15: @fontsource/fraunces and @fontsource/instrument-sans (npm, offline-safe),
        // registered by name in layout.tsx — not next/font/google.
        display: ["Fraunces", "Georgia", "serif"],
        body: ["Instrument Sans", "system-ui", "sans-serif"],
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        rise: "rise .7s cubic-bezier(.2,.7,.2,1) both",
        draw: "draw 2s cubic-bezier(.4,0,.2,1) .35s both",
        ping2: "ping2 1.8s ease-out infinite",
        grow: "grow 1.1s cubic-bezier(.2,.7,.2,1) .4s both",
        breathe: "breathe 2.4s ease-in-out infinite",
        fadein: "fadein .5s ease both",
        drift: "drift 14s ease-in-out infinite",
        drift2: "drift2 17s ease-in-out infinite",
        bob: "bob 3.2s ease-in-out infinite",
      },
      keyframes: {
        rise: { from: { opacity: 0, transform: "translateY(14px)" }, to: { opacity: 1, transform: "none" } },
        draw: { from: { strokeDashoffset: 900 }, to: { strokeDashoffset: 0 } },
        ping2: { "0%": { transform: "scale(1)", opacity: 0.55 }, "100%": { transform: "scale(2.8)", opacity: 0 } },
        grow: { from: { transform: "scaleX(0)" }, to: { transform: "scaleX(1)" } },
        breathe: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.45 } },
        fadein: { from: { opacity: 0 }, to: { opacity: 1 } },
        drift: {
          "0%,100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(40px,-30px) scale(1.15)" },
        },
        drift2: {
          "0%,100%": { transform: "translate(0,0) scale(1.1)" },
          "50%": { transform: "translate(-50px,25px) scale(.92)" },
        },
        bob: { "0%,100%": { transform: "translateY(0)" }, "50%": { transform: "translateY(-4px)" } },
      },
    },
  },
  plugins: [],
};
