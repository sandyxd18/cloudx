FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY image/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/auth_middleware.py ./app/auth_middleware.py
COPY shared/request_id_middleware.py ./shared/request_id_middleware.py
COPY shared/metrics.py ./shared/metrics.py
COPY shared/service_client.py ./shared/service_client.py
COPY image/app ./app

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}"]
