# syntax=docker/dockerfile:1
# Production web app: the Vite build served by nginx on port 8080 (Cloud Run's convention).
# Development does not use this file: docker-compose.yml runs the Vite dev server.
FROM node:24.21.0-bookworm-slim AS build
WORKDIR /app/frontend
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 8080
