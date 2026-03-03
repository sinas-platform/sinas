# Stage 1: Build the frontend
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Replace local file: deps with npm versions for Docker build
RUN sed -i 's|"file:../../sinas-js"|"*"|g' package.json && \
    sed -i 's|"file:../../sinas-ui"|"*"|g' package.json

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
