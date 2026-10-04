FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV PYTHONPATH=/app/backend

EXPOSE 8000

# Run migrations/seeding once, then start a single worker (1-CPU host;
# one worker also keeps the in-memory session cache and rate limiter consistent)
CMD ["sh", "-c", "python -m app.startup && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips '*' --log-level warning"]
