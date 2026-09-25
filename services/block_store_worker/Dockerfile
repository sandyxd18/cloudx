FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System packages needed for LVM and Ceph operations
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        lvm2 \
        qemu-utils \
        ceph-common \
        curl \
        ca-certificates \
    && (curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc || curl -fsSL https://dl.minio.org.cn/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc) \
    && chmod +x /usr/local/bin/mc || true \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY block_store_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared RPC library
COPY shared/rpc.py ./shared/rpc.py
COPY block_store_worker/app ./app

CMD ["python", "-m", "app.main"]
