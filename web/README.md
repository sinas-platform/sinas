# Sinas web apps

An npm workspace for Sinas frontends other than the console.

- `packages/ui` (`@sinas/ui`): what every app shares. That is the HTTP
  client with token refresh, sign-in, the theme tokens and component
  classes, toasts, and the sidebar shell. Apps import its TypeScript
  source directly, so it has no build step.
- `apps/switchboard` (`@sinas/switchboard`): publish functions, queries and
  pipelines to outside agents, manage how they sign in, and read the audit
  log. Served under `/switchboard/`.

```bash
cd web
npm install
npm run dev:switchboard      # http://localhost:51246/switchboard/ against a backend on :8000
npm run build                # typecheck and build every app
npm test
```

`VITE_API_URL` points an app at another backend.

The console still lives in `../console`. It moves into `apps/` after the
release, at which point it can share `@sinas/ui` too.
