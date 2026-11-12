/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// En desarrollo, Vite reenvía la API al gateway: mismo origen que en producción,
// donde el propio gateway sirve la web. FOGAR_GATEWAY_URL apunta a otro gateway.
const target = process.env.FOGAR_GATEWAY_URL ?? 'http://127.0.0.1:8004'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: {
      '/admin': { target, changeOrigin: true },
      '/ready': { target, changeOrigin: true },
      '/healthz': { target, changeOrigin: true },
      '/v1': { target, changeOrigin: true },
      '/ws': { target, changeOrigin: true, ws: true },
    },
  },
  test: {
    environment: 'jsdom',
  },
})
