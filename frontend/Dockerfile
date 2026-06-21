# ── Stage 1: Build ──────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

ARG VITE_API_BASE_URL=http://localhost:8081
ARG VITE_API_TOKEN=poc-demo-token-2024
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_API_TOKEN=$VITE_API_TOKEN

COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# ── Stage 2: Nginx Runtime ────────────────────────────────────
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# SPA routing — redirect all 404s to index.html
RUN echo 'server { \
  listen 80; \
  root /usr/share/nginx/html; \
  index index.html; \
  location / { try_files $uri $uri/ /index.html; } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
