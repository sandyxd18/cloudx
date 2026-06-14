FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System packages needed for LVM and Ceph operations
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        lvm2 \
        qemu-utils \
        ceph-common \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared RPC library
COPY ../shared/rpc.py ./shared/rpc.py
COPY app ./app

CMD ["python", "-m", "app.main"]
