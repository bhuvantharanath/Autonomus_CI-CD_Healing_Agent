#!/bin/sh
set -e

# ── Generate runtime config for the React app ──────────────────────
# This creates /config.js loaded by index.html before the app bundle.
# It injects VITE_API_URL so the SPA knows where the backend lives.

API_URL="${VITE_API_URL:-http://localhost:8000}"

cat > /usr/share/nginx/html/config.js <<EOF
window.__ENV__ = {
  VITE_API_URL: "${API_URL}"
};
EOF

echo "==> config.js generated with VITE_API_URL=${API_URL}"

# ── Generate nginx config from template ────────────────────────────
envsubst '${PORT}' < /etc/nginx/templates/default.conf.template \
  > /etc/nginx/conf.d/default.conf

echo "==> nginx listening on port ${PORT}"

# ── Start nginx ────────────────────────────────────────────────────
exec nginx -g 'daemon off;'
