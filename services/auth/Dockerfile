FROM python:3.12-slim-bookworm
WORKDIR /app
COPY auth/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/metrics.py ./shared/metrics.py
COPY auth/app ./app
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --workers 1"]
