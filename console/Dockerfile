# Stage 1: Build the frontend
FROM node:20-alpine AS builder

WORKDIR /app

# Copy SDK and UI package dist + package.json (needed for file: deps)
COPY sinas-js/package.json sinas-js/package.json
COPY sinas-js/dist sinas-js/dist
COPY sinas-ui/package.json sinas-ui/package.json
COPY sinas-ui/dist sinas-ui/dist

# Copy console package.json only (no lockfile — file: paths are rewritten)
COPY SINAS/console/package.json ./

# Rewrite file: paths to point at sibling dirs within /app
RUN sed -i 's|file:../../sinas-js|file:./sinas-js|g' package.json && \
    sed -i 's|file:../../sinas-ui|file:./sinas-ui|g' package.json

# Install dependencies
RUN npm install

# Copy console source code (exclude package.json to keep rewritten version)
COPY SINAS/console/src ./src
COPY SINAS/console/public ./public
COPY SINAS/console/index.html ./
COPY SINAS/console/tsconfig*.json ./
COPY SINAS/console/vite.config.ts ./
COPY SINAS/console/postcss.config.js ./
COPY SINAS/console/tailwind.config.js ./
COPY SINAS/console/eslint.config.js ./

# Build the application
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built files from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY SINAS/console/nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
