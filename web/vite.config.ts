import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build emits to web/dist, which the FastAPI server mounts at "/".
// During `npm run dev`, /api is proxied to the local backend on :8000.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
