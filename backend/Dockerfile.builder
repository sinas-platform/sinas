FROM node:20-slim
WORKDIR /app
RUN npm install esbuild@0.28.0
COPY builder_server.js /app/server.js
EXPOSE 3000
CMD ["node", "/app/server.js"]
