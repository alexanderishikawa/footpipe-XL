FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package dependencies first for better layer caching.
COPY pyproject.toml /app/pyproject.toml
COPY pipeline /app/pipeline
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini

RUN pip install --upgrade pip && pip install ".[dev]"

COPY entrypoint-api.sh /app/entrypoint-api.sh
COPY entrypoint-worker.sh /app/entrypoint-worker.sh
RUN chmod +x /app/entrypoint-api.sh /app/entrypoint-worker.sh

EXPOSE 8080

CMD ["/app/entrypoint-api.sh"]
