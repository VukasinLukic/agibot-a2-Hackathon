import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8070"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Build to ../dist/ for FastAPI to serve
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
  // Development server with API proxy
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
