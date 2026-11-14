/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The page is served by plugin/dashboard.py: index.html at /, everything else under /static/plugin/.
// `pnpm dev` proxies the API to a running `claudysha dashboard` (open the dev page with its #token=).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/static/plugin/",
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "../plugin/web",
    emptyOutDir: true,
    modulePreload: { polyfill: false }, // no inline code: the page's CSP allows only its own files
    chunkSizeWarningLimit: 1000,
  },
  server: {
    proxy: { "/api": { target: `http://127.0.0.1:${process.env.CLAUDYSHA_PORT ?? 8765}`, headers: { Host: `127.0.0.1:${process.env.CLAUDYSHA_PORT ?? 8765}` } } },
  },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test-setup.ts"], css: false },
});
