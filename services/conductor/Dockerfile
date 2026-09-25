FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY conductor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared RPC library into app path
COPY shared/rpc.py ./shared/rpc.py
COPY shared/service_client.py ./shared/service_client.py
COPY conductor/app ./app

CMD ["python", "-m", "app.main"]
