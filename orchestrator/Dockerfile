FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && npm install -g @openai/codex \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY alembic.ini .
COPY alembic/ ./alembic/
COPY app/ ./app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# NOTE: runs as root because it needs Docker socket access for container management.
# The container itself is isolated — security is enforced at the Docker level.

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--timeout-graceful-shutdown", "3"]

# Production target
FROM base AS production

# IMPORTANT: exactly ONE worker. The app lifespan (app/main.py) starts many
# process-wide singletons — the global Telegram getUpdates poller, the schedule
# runner, the daily self-test, the skill crawler, OAuth/token refreshers, the
# disk monitor. Running >1 uvicorn worker (or >1 replica) duplicates all of
# them: schedules fire twice and Telegram raises "terminated by other
# getUpdates request" with every message delivered twice (issue #319).
# Scaling horizontally requires a cluster-wide lock per singleton first
# (see app/telegram/bot_manager.py).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
