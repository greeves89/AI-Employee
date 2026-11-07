FROM python:3.12-slim AS base

# libxmlsec1 wird fuer SAML gebraucht: die Signaturpruefung von XML-Assertions laeuft
# ueber xmlsec, nicht ueber Eigenbau — dort sitzen sonst Signature-Wrapping-Angriffe.
# Die -dev-Pakete und pkg-config stehen dabei, damit pip das xmlsec-Modul auch dann
# uebersetzen kann, wenn fuer die Plattform (z. B. arm64 auf dem Pi) kein fertiges
# Rad vorliegt.
#
# Node.js kommt wie im Agent-Image aus NodeSource 22: Debians nodejs ist v20, die
# Claude-Code-CLI verlangt Node >= 22. --engine-strict macht aus der npm-Warnung
# (EBADENGINE) einen Abbruch -- verlangt eine kuenftige CLI mehr, scheitert der Bau
# sichtbar, statt ein Image zu liefern, das erst zur Laufzeit bricht.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git \
    pkg-config gcc libc6-dev \
    libxml2 libxml2-dev libxslt1-dev zlib1g-dev \
    libxmlsec1 libxmlsec1-dev libxmlsec1-openssl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g --engine-strict @anthropic-ai/claude-code \
    && npm install -g --engine-strict @openai/codex \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .

# lxml und xmlsec MUESSEN gegen dieselbe libxml2 gebaut sein, sonst scheitert der
# erste SAML-Aufruf mit "lxml & xmlsec libxml2 library version mismatch" — und zwar
# erst zur Laufzeit, nicht beim Bauen. Fertige Raeder bringen jeweils ihre eigene
# libxml2 mit, deshalb werden beide hier aus den Quellen gegen die libxml2 des Images
# uebersetzt. Muss VOR dem restlichen Install stehen, damit pip sie nicht doch als
# Rad nachzieht. Der Quellbau braucht einen Compiler — deshalb stehen gcc und
# libc6-dev oben mit in der apt-Zeile, dazu libxslt1-dev und zlib1g-dev — lxml
# linkt gegen -lxslt/-lexslt/-lz. Der Satz wurde in einem Wegwerf-Container auf
# arm64 nachgestellt, bevor er hier landete: danach laden lxml und xmlsec beide
# gegen libxml2 2.9.14.
RUN pip install --no-cache-dir --no-binary lxml,xmlsec lxml xmlsec

RUN pip install --no-cache-dir -e .

COPY alembic.ini .
COPY alembic/ ./alembic/
COPY app/ ./app/
# Seccomp-Profil der Agenten: der Orchestrator gibt es mit, der Docker-Wächter (gleiches
# Abbild, ohne Code vom Host) lässt nur genau dieses zu.
COPY seccomp-profile.json ./seccomp-profile.json

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# NOTE: runs as root because it needs Docker socket access for container management.
# The container itself is isolated — security is enforced at the Docker level.

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--timeout-graceful-shutdown", "3", "--no-proxy-headers"]

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
#
# --no-proxy-headers: uvicorn trusts NO forwarding headers itself. The app's
# outermost middleware (core/vertraute_proxys.VertrauterProxy) applies
# X-Forwarded-For/-Proto only when the real peer is the ingress proxy
# (TRUSTED_PROXY_HOSTS, default ai-employee-caddy) — so Caddy's X-Forwarded-Proto
# still makes redirects https://, but an agent on the Docker network can no longer
# claim an arbitrary client address (was --forwarded-allow-ips=* until 1.439).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
