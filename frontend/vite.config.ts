import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080', // the api container (compose.yaml)
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // The API serves seedcast.html at /.
    rollupOptions: {
      input: { seedcast: 'seedcast.html' },
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
  },
})
