# Frontend: node build stage -> static nginx serve.
# VITE_API_URL is baked at build time (Vite inlines env) — the browser calls
# the API directly, so it must be a host-reachable URL, not a compose hostname.

FROM node:20-slim AS builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build


FROM nginx:1.27-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
