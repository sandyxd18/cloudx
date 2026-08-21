FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ovn-central openvswitch-switch openvswitch-common netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY networking/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/auth_middleware.py ./app/auth_middleware.py
COPY shared/request_id_middleware.py ./shared/request_id_middleware.py
COPY shared/metrics.py ./shared/metrics.py
COPY networking/app ./app

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-127.0.0.1} --port ${PORT:-8000}"]
