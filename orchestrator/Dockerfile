FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY alembic.ini .
COPY alembic/ ./alembic/
COPY app/ ./app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--timeout-graceful-shutdown", "3"]

# Production target
FROM base AS production

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
