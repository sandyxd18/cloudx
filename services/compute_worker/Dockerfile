FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cloud-image-utils \
        genisoimage \
        iproute2 \
        libvirt-dev \
        openvswitch-switch \
        pkg-config \
        qemu-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY compute_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared RPC library
COPY shared/rpc.py ./shared/rpc.py
COPY compute_worker/app ./app

CMD ["python", "-m", "app.main"]
