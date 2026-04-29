FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps required by psycopg2-binary/asyncpg and healthchecks
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy source
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

# The app imports modules as top-level packages (config, models, ...), so
# src/app must be on PYTHONPATH.
ENV PYTHONPATH=/app/src/app

WORKDIR /app/src/app

EXPOSE 8004

# Run migrations on startup, then the API
CMD ["sh", "-c", "cd /app && alembic upgrade head && cd /app/src/app && uvicorn main:app --host 0.0.0.0 --port 8004 --workers 2"]
