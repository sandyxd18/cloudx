FROM python:3.12-slim-bookworm

WORKDIR /app

COPY orchestration_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules as a package
COPY shared/ ./shared/

# Copy service code
COPY orchestration_worker/app ./app

# Ensure Python can find shared package
ENV PYTHONPATH=/app

CMD ["python", "-m", "app.main"]