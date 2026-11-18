# Stage 1: Build the frontend
# Debian-based (glibc), not alpine: the musl build of rollup's native parser
# currently fails `vite build` with "Code is nested too deeply" on
# react-dom. Only this stage changes; the image ships nginx:alpine below.
FROM node:20-slim AS builder

# Set to "true" to use a locally built @sinas/sdk instead of npm
ARG USE_LOCAL_PACKAGES=false

# Backend URL baked into the build (skips the same-origin auto-detect fallback)
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

WORKDIR /app

# Copy package.json only (lockfile has file: paths that get rewritten)
COPY package.json ./

# Copy local packages (only used when USE_LOCAL_PACKAGES=true)
COPY sinas-sdk.tgz* ./

# Replace file: deps with either local tarballs or npm latest
RUN if [ "$USE_LOCAL_PACKAGES" = "true" ]; then \
      sed -i 's|"file:../../sinas-js"|"file:./sinas-sdk.tgz"|g' package.json; \
    else \
      sed -i 's|"file:../../sinas-js"|"latest"|g' package.json; \
    fi

# Install dependencies
RUN npm install

# Copy source code
COPY src ./src
COPY public ./public
COPY index.html ./
COPY tsconfig*.json ./
COPY vite.config.ts ./
COPY postcss.config.js ./
COPY eslint.config.js ./

# Build the application
RUN npm run build

# Stage 2: Serve with Nginx, unprivileged
FROM nginx:alpine

# Copy built files from builder
COPY --from=builder /app/dist /usr/share/nginx/html/ui

# Copy nginx configuration (listens on 8080: no privileged port)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Run as nginx's own uid (101), group 0, arbitrary-UID friendly: drop the
# `user` directive (only root can switch users), move the pid file to /tmp,
# and make nginx's temp/cache directory owned by group 0 and group-writable.
# Writable at runtime: /var/cache/nginx and /tmp (the Helm chart mounts an
# emptyDir on each). Logs go to stdout/stderr. No sysctl or capability is
# needed for port 8080.
RUN sed -i -e '/^user /d' -e 's#^pid .*#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
    && grep -q '^pid /tmp/nginx.pid;' /etc/nginx/nginx.conf \
    && chown -R 101:0 /var/cache/nginx && chmod -R g=u /var/cache/nginx
USER 101

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
