FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies for libvirt (build + runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvirt-dev \
    libvirt0 \
    gcc \
    libc6-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY telemetry_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules as a package
COPY shared/ ./shared/

# Copy service code
COPY telemetry_worker/app ./app

# Ensure Python can find shared package
ENV PYTHONPATH=/app

CMD ["python", "-m", "app.main"]