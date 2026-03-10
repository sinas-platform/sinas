# Stage 1: Build the frontend
FROM node:20-alpine AS builder

# Set to "true" to use locally built @sinas/sdk and @sinas/ui instead of npm
ARG USE_LOCAL_PACKAGES=false

WORKDIR /app

# Copy package.json only (lockfile has file: paths that get rewritten)
COPY package.json ./

# Copy local packages (only used when USE_LOCAL_PACKAGES=true)
COPY sinas-sdk.tgz* sinas-ui.tgz* ./

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
COPY src ./src
COPY public ./public
COPY index.html ./
COPY tsconfig*.json ./
COPY vite.config.ts ./
COPY postcss.config.js ./
COPY tailwind.config.js ./
COPY eslint.config.js ./

# Build the application
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built files from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
