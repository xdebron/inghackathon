import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Tünel (trycloudflare vb.) üzerinden demo için tüm host adlarına izin verilir
    allowedHosts: true,
    proxy: { '/api': 'http://127.0.0.1:8010' },
  },
})
