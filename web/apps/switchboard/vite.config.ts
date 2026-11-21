import { defineConfig, type Connect, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// `/switchboard` (no slash) into the app, as the nginx config does.
const bareBaseRedirect: Connect.NextHandleFunction = (req, res, next) => {
  const url = req.url ?? ''
  if (url === '/switchboard' || url.startsWith('/switchboard?')) {
    res.statusCode = 302
    res.setHeader('Location', `/switchboard/${url.slice('/switchboard'.length)}`)
    res.end()
    return
  }
  next()
}

const bareBase: Plugin = {
  name: 'sinas-bare-base-redirect',
  configureServer: (server) => { server.middlewares.use(bareBaseRedirect) },
  configurePreviewServer: (server) => { server.middlewares.use(bareBaseRedirect) },
}

// Served under /switchboard/ so it can share an origin (and a sign-in) with
// the console and the API behind one proxy.
export default defineConfig({
  base: '/switchboard/',
  plugins: [react(), bareBase],
  resolve: {
    dedupe: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
  },
  server: {
    port: 51246,
  },
  preview: {
    port: 51246,
  },
})
