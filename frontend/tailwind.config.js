/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"] },
      fontSize: { "2xs": ["0.6875rem", { lineHeight: "1rem" }] },
      colors: {
        // one accent, used sparingly: primary actions, the active nav item, links and focus rings
        brand: { 50: "#f1f4ff", 100: "#e2e8ff", 200: "#c7d1fe", 300: "#a3b1fb", 500: "#4c5fe8", 600: "#3a48cf", 700: "#2f3aa8", 900: "#1c2260" },
        canvas: "#f7f7f8",
      },
      boxShadow: { card: "0 1px 0 0 rgb(15 23 42 / 0.03)", pop: "0 8px 24px -6px rgb(15 23 42 / 0.18), 0 2px 6px -2px rgb(15 23 42 / 0.08)" },
      keyframes: { "fade-in": { from: { opacity: "0", transform: "translateY(2px)" }, to: { opacity: "1", transform: "none" } } },
      animation: { "fade-in": "fade-in 160ms ease-out" },
    },
  },
  plugins: [],
};
