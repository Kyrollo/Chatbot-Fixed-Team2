import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      // Proxy API calls to the respective backend services
      "/domains": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
      "/ingest": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
      "/api/v1/retrieve": {
        target: "http://localhost:8003",
        changeOrigin: true,
      },
      "/generate": {
        target: "http://localhost:8004",
        changeOrigin: true,
      },
      "/evaluate": {
        target: "http://localhost:8005",
        changeOrigin: true,
      },
    },
  },
});
