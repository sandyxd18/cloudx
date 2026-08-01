# Stage 1 — Build React frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy package files first for caching
COPY dashboard/frontend/package.json dashboard/frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps

# Copy source and build
COPY dashboard/frontend/ ./
RUN npm run build

# Stage 2 — Python FastAPI proxy server
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY dashboard/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy FastAPI proxy
COPY dashboard/app/ ./app/

# Copy shared RPC library for event consumer
COPY shared/rpc.py ./shared/rpc.py

# Copy built React app into static dir
COPY --from=frontend-builder /app/frontend/dist ./app/static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-127.0.0.1} --port ${PORT:-8000}"]
