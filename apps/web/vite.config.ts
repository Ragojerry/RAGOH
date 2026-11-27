import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `pnpm dev` proxies the API to a running backend (default: local uvicorn).
const target = process.env.RAGOH_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target, changeOrigin: true } } },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 1200, // maplibre-gl is ~800 kB on its own; it is split out below
    rollupOptions: {
      output: { manualChunks: (id: string) => (id.includes("maplibre-gl") ? "maplibre" : undefined) },
    },
  },
  test: { environment: "jsdom" },
});
