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
      '/domains': 'http://localhost:8001',
      '/monitoring': 'http://localhost:8001',
      '/ingest': 'http://localhost:8002',
      '/retrieve': 'http://localhost:8003',
      '/generate': 'http://localhost:8004',
      '/query': 'http://localhost:8004',
      '/evaluate': 'http://localhost:8005',
    }
  }
})
