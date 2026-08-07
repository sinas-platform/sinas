# Build context is the REPO ROOT (not ./console) so this image can bundle
# the Studio app alongside the console. Build with:
#   docker build -f console/Dockerfile .

# Stage 1: Build the console
FROM node:20-alpine AS builder

# Set to "true" to use locally built @sinas/sdk and @sinas/ui instead of npm
ARG USE_LOCAL_PACKAGES=false

WORKDIR /app

# Copy package.json only (lockfile has file: paths that get rewritten)
COPY console/package.json ./

# Copy local packages (only used when USE_LOCAL_PACKAGES=true)
COPY console/sinas-sdk.tgz* console/sinas-ui.tgz* ./

# Replace file: deps with either local tarballs or npm latest
RUN if [ "$USE_LOCAL_PACKAGES" = "true" ]; then \
      sed -i 's|"file:../../sinas-js"|"file:./sinas-sdk.tgz"|g' package.json && \
      sed -i 's|"file:../../sinas-ui"|"file:./sinas-ui.tgz"|g' package.json; \
    else \
      sed -i 's|"file:../../sinas-js"|"latest"|g' package.json && \
      sed -i 's|"file:../../sinas-ui"|"latest"|g' package.json; \
    fi

# Install dependencies
RUN npm install

# Copy source code
COPY console/src ./src
COPY console/public ./public
COPY console/index.html ./
COPY console/tsconfig.json console/tsconfig.app.json console/tsconfig.node.json ./
COPY console/vite.config.ts ./
COPY console/postcss.config.js ./
COPY console/tailwind.config.js ./
COPY console/eslint.config.js ./

# Build the application
RUN npm run build

# Stage 2: Build Studio (served under /studio/, see studio/README.md §8)
FROM node:20-alpine AS studio-builder

# Mirror the repo layout: the app imports ../../packages/studio-runtime.yaml
# (relative to studio/app) so setup installs a version-matched package.
WORKDIR /src/studio/app

COPY studio/app/package.json studio/app/package-lock.json ./
RUN npm ci

COPY studio/app/ ./
COPY studio/packages/ /src/studio/packages/
RUN npm run build

# Stage 3: Serve with Nginx
FROM nginx:alpine

# Copy built files from builders
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=studio-builder /src/studio/app/dist /usr/share/nginx/html/studio

# Copy nginx configuration
COPY console/nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
