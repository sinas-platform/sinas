import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // The console is served under /ui so the ingress can route by path with no
  // per-endpoint allowlist: /ui -> console, everything else -> backend.
  base: '/ui/',
  plugins: [react()],
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  server: {
    port: 51245,
  },
})
