# ── Stage 1: Build ──────────────────────────────────────────
FROM node:18-slim AS build

WORKDIR /app

# Build-time arg — set VITE_API_URL so the React app knows the backend
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

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

# Write nginx config template with $PORT placeholder
RUN printf 'server {\n\
    listen ${PORT};\n\
    listen [::]:${PORT};\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {\n\
        expires 1y;\n\
        add_header Cache-Control "public, immutable";\n\
    }\n\
}\n' > /etc/nginx/templates/default.conf.template

# Default port for local dev; Render overrides via $PORT
ENV PORT=80

EXPOSE 80

# envsubst replaces ${PORT} in the template, then starts nginx
CMD sh -c "envsubst '\$PORT' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"
