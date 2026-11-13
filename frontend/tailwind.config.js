/** @type {import('tailwindcss').Config} */
// "Ink & Petrol" — the V2 visual system. `slate` is redefined as the ink scale so every screen moves to the same neutrals,
// and `brand` is petrol: the one accent, used for primary actions, active navigation, links and focus.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["IBM Plex Sans", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
        "title": ["1.375rem", { lineHeight: "1.75rem", letterSpacing: "-0.012em" }],
        "display": ["1.75rem", { lineHeight: "2.125rem", letterSpacing: "-0.018em" }],
      },
      colors: {
        slate: {
          50: "#f5f6f6", 100: "#eceff0", 200: "#dce1e4", 300: "#bcc5cb", 400: "#8a97a1", 500: "#64737f",
          600: "#4b5965", 700: "#3a4752", 800: "#24313c", 900: "#15212c", 950: "#0e1720",
        },
        brand: {
          50: "#ebf5f6", 100: "#d3e9ec", 200: "#a9d2d8", 300: "#75b4be", 400: "#3f97a5", 500: "#1b7e8c",
          600: "#136977", 700: "#0f5562", 800: "#0c4550", 900: "#0a333b",
        },
        ink: { DEFAULT: "#0e1720", raised: "#16222d", line: "#243442", muted: "#8597a5" },
        saffron: { 50: "#fbf4e6", 500: "#c98a1c", 700: "#8f6212" },
        plum: { 50: "#f5eff8", 500: "#7b4f8e", 700: "#5b3a6a" },
        canvas: "#f2f3f1",
      },
      borderRadius: { DEFAULT: "4px", md: "6px", lg: "10px", xl: "12px" },
      boxShadow: {
        card: "0 1px 0 0 rgb(14 23 32 / 0.04)",
        pop: "0 12px 32px -8px rgb(14 23 32 / 0.28), 0 2px 6px -2px rgb(14 23 32 / 0.10)",
        inset: "inset 0 0 0 1px rgb(14 23 32 / 0.06)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(2px)" }, to: { opacity: "1", transform: "none" } },
        "dialog-in": { from: { opacity: "0", transform: "translateY(6px) scale(0.985)" }, to: { opacity: "1", transform: "none" } },
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
      },
      animation: {
        "fade-in": "fade-in 160ms ease-out",
        "dialog-in": "dialog-in 180ms cubic-bezier(.2,.8,.2,1)",
        shimmer: "shimmer 1.6s linear infinite",
      },
      maxWidth: { content: "1280px", read: "46rem" },
    },
  },
  plugins: [],
};
