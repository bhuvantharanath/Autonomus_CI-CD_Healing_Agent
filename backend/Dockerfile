FROM python:3.11-slim

# git is required for clone/branch/commit/push operations
# Node.js is needed for JS/TS syntax checking (node --check)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Set default git identity so commits work inside the container
RUN git config --global user.email "self-healing-bot@automated.dev" \
 && git config --global user.name "Self-Healing Bot"

WORKDIR /app

# Copy and install requirements first (cache layer)
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy monorepo modules needed by the backend
COPY backend/ /app/
COPY agents/ /app/agents/
COPY shared/ /app/shared/
COPY sandbox/ /app/sandbox/

EXPOSE 8000

# Render injects $PORT at runtime; default to 8000 for local dev
ENV PORT=8000
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
