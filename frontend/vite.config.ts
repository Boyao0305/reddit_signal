import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // The public Seedcast site is a second entry served at /seedcast.html.
    rollupOptions: {
      input: { main: 'index.html', seedcast: 'seedcast.html' },
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
  },
})
