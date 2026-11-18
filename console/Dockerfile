# Stage 1: Build the frontend — reproducibly, from package-lock.json.
# Debian-based (glibc) rather than alpine: rollup ships a native parser per
# libc, and its musl build is the one that regressed in 4.68.6 ("Code is
# nested too deeply" on react-dom). The lockfile pins rollup anyway; glibc
# keeps a future lockfile bump from tripping over the musl build alone.
# Only this stage; the image ships nginx:alpine below.
FROM node:20-slim AS builder

# Set to "true" to use a locally built @sinas/sdk (sinas-sdk.tgz in the build
# context) instead of the published SINAS_SDK_VERSION.
ARG USE_LOCAL_PACKAGES=false
# Exact published @sinas/sdk to build against; the CI console-build job pins
# the same version (SINAS_SDK_VERSION in .github/workflows/ci.yml, which
# checks the two agree). Bump both on SDK releases.
ARG SINAS_SDK_VERSION=0.7.0

# Backend URL baked into the build (skips the same-origin auto-detect fallback)
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

WORKDIR /app

COPY package.json package-lock.json ./
COPY scripts ./scripts
# Copy local packages (only used when USE_LOCAL_PACKAGES=true)
COPY sinas-sdk.tgz* ./

# package.json links @sinas/sdk to a sibling checkout for development; point
# it at the pinned version (or the tarball), then install exactly the
# lockfile: every other dependency, vite and rollup included, at the version
# package-lock.json records (scripts/use-published-sdk.mjs explains why both
# files are rewritten).
RUN if [ "$USE_LOCAL_PACKAGES" = "true" ]; then SDK="file:./sinas-sdk.tgz"; else SDK="$SINAS_SDK_VERSION"; fi \
    && node scripts/use-published-sdk.mjs "$SDK" \
    && npm install --package-lock-only --no-audit --no-fund \
    && npm ci --no-audit --no-fund \
    && npm ls --depth=0 @sinas/sdk vite && npm ls rollup

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
