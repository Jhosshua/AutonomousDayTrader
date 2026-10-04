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
        // Bright operator dashboard (2026-10-04). Replaces the 2026-09-24 "muted palette".
        // Every colour in app|components|lib|hooks is listed in docs/bright_dashboard/palette_map.md.
        ground: "#EEF1FA",
        ink: "#0E1330",
        muted: "#5B6283",
        line: "#DDE2F2",
        darkcard: "#2B4BFF", // the accent: status strip, selected tab
        gain: "#0A7D53",
        gainbg: "#E9F8F0",
        loss: "#C2300F",
        lossbg: "#FFEFEA",
        lime: "#C6F432",
        warn: "#8A4B00",
        warnbg: "#FFF4DB",
        lavender: "#6D3BFF",
        sage: "#0A7D53",
        terracotta: "#C2300F", // alarms only
      },
      fontFamily: {
        // F15: @fontsource/bricolage-grotesque and @fontsource/instrument-sans (npm, offline-safe),
        // registered by name in layout.tsx, not next/font/google.
        display: ["Bricolage Grotesque", "Instrument Sans", "system-ui", "sans-serif"],
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
        bob: "bob 3.2s ease-in-out infinite",
      },
      keyframes: {
        rise: { from: { opacity: 0, transform: "translateY(14px)" }, to: { opacity: 1, transform: "none" } },
        draw: { from: { strokeDashoffset: 900 }, to: { strokeDashoffset: 0 } },
        ping2: { "0%": { transform: "scale(1)", opacity: 0.55 }, "100%": { transform: "scale(2.8)", opacity: 0 } },
        grow: { from: { transform: "scaleX(0)" }, to: { transform: "scaleX(1)" } },
        breathe: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.45 } },
        fadein: { from: { opacity: 0 }, to: { opacity: 1 } },
        bob: { "0%,100%": { transform: "translateY(0)" }, "50%": { transform: "translateY(-4px)" } },
      },
    },
  },
  plugins: [],
};
