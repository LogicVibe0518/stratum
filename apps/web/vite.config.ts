import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Built into apps/web/dist, which the API serves at /app (apps/api: web_app).
// `npm run dev` proxies the API, so `make api` plus `npm run dev` is the whole loop.
const API = process.env.STRATUM_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: true, target: "es2022" },
  server: {
    port: 5173,
    proxy: {
      "/api": API,
      "/previews": API,
      "/fonts": API,
    },
  },
  test: {
    // Pure logic runs in node; a test that needs a DOM names jsdom at its top.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
