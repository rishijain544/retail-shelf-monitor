/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // Expose to local network (required for Docker port forwarding)
    port: 5173,
    strictPort: true,
    // Vite does not serve server-root-relative routes (e.g. /uploads/<id>.jpg,
    // /api/*) itself. Forward them to the backend so that resolveImageUrl()
    // (which rebuilds absolute image URLs) actually works in dev. The backend
    // answers /uploads/<uuid>.jpg with HTTP 200 and image/jpeg.
    proxy: {
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})

