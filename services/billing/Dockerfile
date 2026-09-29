FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies for WeasyPrint
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    libfreetype6 \
    libpng16-16 \
    libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*

COPY billing/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules as a package
COPY shared/ ./shared/

# Copy service code
COPY billing/app ./app

# Ensure Python can find shared package
ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8011}"]