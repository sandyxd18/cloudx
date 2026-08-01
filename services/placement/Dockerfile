FROM python:3.12-slim-bookworm
WORKDIR /app
COPY placement/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/auth_middleware.py ./app/auth_middleware.py
COPY placement/app ./app
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-127.0.0.1} --port ${PORT:-8000}"]
