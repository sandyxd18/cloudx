FROM python:3.12-slim-bookworm
WORKDIR /app
COPY placement/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/auth_middleware.py ./app/auth_middleware.py
COPY shared/metrics.py ./shared/metrics.py
COPY shared/service_client.py ./shared/service_client.py
COPY placement/app ./app
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}"]
