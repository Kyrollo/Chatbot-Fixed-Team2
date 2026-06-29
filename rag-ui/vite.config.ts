import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'

export default defineConfig(({ mode }) => {
  // Load env file from parent directory (workspace root)
  const env = loadEnv(mode, '../', '')
  const vitePort = parseInt(env.VITE_PORT || '5173', 10)
  const backendPort = env.DOMAIN_SERVICE_PORT || '8001'
  const backendUrl = `http://localhost:${backendPort}`

  return {
    envDir: '../',
    plugins: [
      react(),
      basicSsl()
    ],
    server: {
      port: vitePort,
      proxy: {
        '/domains': backendUrl,
        '/monitoring': backendUrl,
        '/ingest': backendUrl,
        '/retrieve': backendUrl,
        '/generate': backendUrl,
        '/query': backendUrl,
        '/evaluate': backendUrl,
        '/moderation': backendUrl,
      }
    }
  }
})