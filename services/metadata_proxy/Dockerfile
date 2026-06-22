FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY metadata_proxy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY metadata_proxy/app ./app

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${METADATA_PROXY_PORT:-8776}"]
