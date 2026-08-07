import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Served at /studio/ on the workspace origin by default (bundled mode);
// a standalone deployment mounts it at /studio/ too.
export default defineConfig({
  base: '/studio/',
  plugins: [react()],
  server: { port: 5180 },
});
