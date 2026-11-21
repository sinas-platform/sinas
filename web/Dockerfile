# One image per app in this workspace: build with the workspace as context.
#
#   docker build -f web/Dockerfile --build-arg APP=switchboard web
#
FROM node:22-alpine AS builder

ARG APP=switchboard
# Backend URL baked into the build (skips the same-origin auto-detect).
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL
# Where "Open Sinas console" points, if not the conventional place.
ARG VITE_CONSOLE_URL
ENV VITE_CONSOLE_URL=$VITE_CONSOLE_URL

WORKDIR /app
COPY package.json package-lock.json tsconfig.base.json ./
COPY packages ./packages
COPY apps/${APP}/package.json ./apps/${APP}/package.json
RUN npm ci --no-audit --no-fund -w @sinas/${APP} --include-workspace-root

COPY apps/${APP} ./apps/${APP}
RUN npm run build -w @sinas/${APP}

FROM nginx:alpine
ARG APP=switchboard
COPY --from=builder /app/apps/${APP}/dist /usr/share/nginx/html/${APP}
COPY apps/${APP}/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
