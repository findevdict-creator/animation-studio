import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server proxies /api to the backend, so the SPA and the API share an origin in
// development. Object storage is reached directly through presigned URLs.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: process.env.STUDIO_API_URL ?? "http://localhost:8000", changeOrigin: true } },
  },
  test: { environment: "jsdom", globals: false },
});
