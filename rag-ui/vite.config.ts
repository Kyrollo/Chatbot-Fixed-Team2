// vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Local dev (run_services.py): each service runs on its own port.
      // When the Traefik gateway is used later, point all of these at
      // 'http://localhost:80' instead.
      '/domains': 'http://localhost:8000',
      '/monitoring': 'http://localhost:8000',
      '/ingest': 'http://localhost:8000',
      '/retrieve': 'http://localhost:8000',
      '/generate': 'http://localhost:8000',
      '/query': 'http://localhost:8000',
      '/evaluate': 'http://localhost:8000',
      '/moderation': 'http://localhost:8000',
    }
  }
})