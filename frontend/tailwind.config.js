/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
      colors: { brand: { 50: "#eef4ff", 100: "#dbe7ff", 500: "#4f6ef7", 600: "#3d57e0", 700: "#3146b8" } },
    },
  },
  plugins: [],
};
