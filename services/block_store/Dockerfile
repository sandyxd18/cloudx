FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY block_store/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/auth_middleware.py ./app/auth_middleware.py
COPY shared/rpc.py ./shared/rpc.py
COPY block_store/app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
