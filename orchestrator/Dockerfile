FROM python:3.12-slim AS base

# libxmlsec1 wird fuer SAML gebraucht: die Signaturpruefung von XML-Assertions laeuft
# ueber xmlsec, nicht ueber Eigenbau — dort sitzen sonst Signature-Wrapping-Angriffe.
# Die -dev-Pakete und pkg-config stehen dabei, damit pip das xmlsec-Modul auch dann
# uebersetzen kann, wenn fuer die Plattform (z. B. arm64 auf dem Pi) kein fertiges
# Rad vorliegt.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git nodejs npm \
    pkg-config libxml2 libxml2-dev libxmlsec1 libxmlsec1-dev libxmlsec1-openssl \
    && npm install -g @anthropic-ai/claude-code \
    && npm install -g @openai/codex \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .

# lxml und xmlsec MUESSEN gegen dieselbe libxml2 gebaut sein, sonst scheitert der
# erste SAML-Aufruf mit "lxml & xmlsec libxml2 library version mismatch" — und zwar
# erst zur Laufzeit, nicht beim Bauen. Fertige Raeder bringen jeweils ihre eigene
# libxml2 mit, deshalb werden beide hier aus den Quellen gegen die libxml2 des Images
# uebersetzt. Muss VOR dem restlichen Install stehen, damit pip sie nicht doch als
# Rad nachzieht.
RUN pip install --no-cache-dir --no-binary lxml,xmlsec lxml xmlsec

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
