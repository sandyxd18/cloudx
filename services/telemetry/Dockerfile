FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvirt0 \
    && rm -rf /var/lib/apt/lists/*

COPY telemetry/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules as a package
COPY shared/ ./shared/

# Copy service code
COPY telemetry/app ./app

# Ensure Python can find shared package
ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8010}"]