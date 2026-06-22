FROM python:3.12-slim-bookworm
WORKDIR /app
COPY placement/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/auth_middleware.py ./app/auth_middleware.py
COPY placement/app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
