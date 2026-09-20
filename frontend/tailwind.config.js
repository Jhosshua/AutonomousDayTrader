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
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [],
};
