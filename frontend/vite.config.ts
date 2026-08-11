import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    // The dev compose overlay (docker-compose.dev.yml) is reached by other
    // containers — notably qa/run.sh's Playwright runner — via the compose
    // service DNS name "frontend", which Vite 5's host-check blocks by
    // default (only localhost/IPs are allowed out of the box).
    allowedHosts: ["frontend"],
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET || "http://localhost:8081",
        changeOrigin: true,
      },
    },
  },
});
