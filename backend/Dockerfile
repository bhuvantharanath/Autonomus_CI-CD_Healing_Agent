FROM python:3.11-slim

# git is required for clone/branch/commit/push operations
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

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
