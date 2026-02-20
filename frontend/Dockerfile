# ── Stage 1: Build ──────────────────────────────────────────
FROM node:20-slim AS build

WORKDIR /app

# Install dependencies (cache layer)
COPY package.json package-lock.json* ./
RUN npm install

# Copy source and build
COPY . .
RUN npm run build

# ── Stage 2: Serve with nginx ───────────────────────────────
FROM nginx:alpine AS production

# Remove default nginx site
RUN rm -rf /usr/share/nginx/html/*

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# Copy nginx config template (envsubst fills $PORT at runtime)
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Default port for local dev; Render overrides via $PORT
ENV PORT=80
ENV VITE_API_URL=http://localhost:8000

EXPOSE 80

# At startup: generate /config.js with runtime env vars, then start nginx
CMD ["/bin/sh", "-c", "echo \"window.__ENV__={VITE_API_URL:\\\"${VITE_API_URL}\\\"}\" > /usr/share/nginx/html/config.js && envsubst '${PORT}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
