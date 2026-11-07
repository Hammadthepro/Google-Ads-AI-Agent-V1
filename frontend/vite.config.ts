import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend so cookies stay same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: { "/api": { target: process.env.VITE_API_TARGET || "http://localhost:8000", changeOrigin: false } },
  },
  preview: { host: "0.0.0.0", port: 4173, proxy: { "/api": { target: process.env.VITE_API_TARGET || "http://localhost:8000" } } },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"], globals: true },
});
