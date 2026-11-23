/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// Two pages share one build:
//   /       the landing page (index.html, static HTML with the design kit in public/assets)
//   /app/   the React dashboard (app/index.html)
// The kit owns /assets/*, so Vite writes its hashed bundles to /static/* instead.

// Deep links such as /app/pipelines/ingest_bookings must load the dashboard in dev and preview,
// the same way nginx and Cloudflare Pages do in production.
function dashboardFallback(): Plugin {
  const rewrite = (req: { url?: string }, _res: unknown, next: () => void) => {
    const path = (req.url ?? "").split("?")[0] ?? "";
    if ((path === "/app" || path.startsWith("/app/")) && !path.includes(".")) {
      req.url = "/app/index.html";
    }
    next();
  };
  return {
    name: "rideops-dashboard-fallback",
    configureServer(server) {
      server.middlewares.use(rewrite);
    },
    configurePreviewServer(server) {
      server.middlewares.use(rewrite);
    },
  };
}

// In development the dashboard calls /api/v1/... and Vite forwards it to FastAPI,
// so the browser sees one origin (no CORS setup needed locally).
// RIDEOPS_API_PROXY points it at another API (e.g. http://localhost:8001 for the Docker API).
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};
const apiTarget = env.RIDEOPS_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), dashboardFallback()],
  appType: "mpa",
  server: {
    port: 5173,
    proxy: { "/api": apiTarget },
  },
  preview: {
    proxy: { "/api": apiTarget },
  },
  build: {
    sourcemap: true,
    assetsDir: "static",
    rollupOptions: {
      input: {
        // Relative to the project root (npm scripts run from frontend/).
        landing: "index.html",
        app: "app/index.html",
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
});
