FROM python:3.12-slim-bookworm

WORKDIR /app

COPY orchestration/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules as a package
COPY shared/ ./shared/

# Copy service code
COPY orchestration/app ./app

# Ensure Python can find shared package
ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8013}"]