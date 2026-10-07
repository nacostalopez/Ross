FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Production start: the port comes from $PORT when the platform sets one (Railway does),
# and --proxy-headers makes the client IP the visitor's rather than the platform's proxy,
# which the per-IP rate limiter depends on. docker-compose overrides this with --reload for dev.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
