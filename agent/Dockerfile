FROM python:3.12-slim AS base

# Version label - change this to trigger update detection for running agents
LABEL ai-employee.version="1.331.1"

# Install system dependencies + Node.js (required for Claude Code CLI)
# Pre-install common tools the agent might need
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    curl \
    git \
    build-essential \
    jq \
    wget \
    unzip \
    python3-venv \
    sudo \
    gosu \
    ripgrep \
    ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally. This version is only the BUILD-TIME
# baseline for a fresh image -- entrypoint.sh re-installs "@latest" on every
# container start (i.e. every agent "Update"), so an image rebuild is no
# longer required just to pick up a newer CLI release.
RUN npm install -g @anthropic-ai/claude-code@2.1.270

# Install OpenAI Codex CLI for ChatGPT-subscription backed agents. Same as
# above: entrypoint.sh keeps this current on every container start.
RUN npm install -g @openai/codex

# Install Hyperframes CLI for agent video rendering
RUN npm install -g hyperframes

# Pre-install Playwright + Chromium for browser automation (Computer-Use Phase 1)
# Only activated when COMPUTER_USE_BROWSER=true — pre-installing avoids runtime download delays
#
# GEMEINSAMER Ablageort statt des Heimatverzeichnisses (21.09.2026, #828):
# Der Download laeuft hier als root, der Container laeuft spaeter als "agent"
# (gosu im Startskript) -- und der Nutzer "agent" existiert an dieser Stelle
# noch gar nicht. Ohne diese Variable landeten 662 MB Browser in
# /root/.cache/ms-playwright, wo der Agent sie WEDER findet NOCH lesen darf
# ("Permission denied"). Ergebnis: Python-Playwright scheiterte hart, und der
# Playwright-MCP (der Weg von Claude Code) musste bei jedem frischen Container
# erneut ~400 MB ziehen -- oder blieb ohne Netz ganz aus.
#
# Der Ort muss VOR dem Download gesetzt sein, sonst wirkt er nicht; das
# abschliessende "chmod a+rX" macht ihn fuer den Agenten-Nutzer lesbar --
# ohne das waere der gemeinsame Ort genauso unerreichbar wie /root/.cache.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    chromium \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && npx -y @playwright/mcp@latest --version 2>/dev/null || true \
    && npx -y playwright install chromium --with-deps 2>/dev/null || true \
    && chmod -R a+rX /opt/ms-playwright 2>/dev/null || true

# Point Puppeteer/hyperframes to the system Chromium (avoids x86_64 download on ARM)
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

# Create non-root user with sudo installed (permissions configured dynamically by orchestrator)
# Container isolation is the real sandbox - sudo rules are set per-agent via permission packages
RUN useradd -m -s /bin/bash agent

# Install Python dependencies
WORKDIR /opt/agent
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app/ ./app/
RUN chmod -R a+rX /opt/agent/app/

# Install ai-team CLI helper (allows agents to call orchestrator API)
COPY scripts/ai-team /usr/local/bin/ai-team
RUN chmod +x /usr/local/bin/ai-team

# Install MCP servers (Model Context Protocol - provides native tools to Claude CLI)
COPY mcp/ /opt/mcp/
RUN cd /opt/mcp && npm install --production

# Create workspace directory owned by agent user
RUN mkdir -p /workspace && chown agent:agent /workspace

# Create .claude directory for session persistence (volume mount inherits ownership)
RUN mkdir -p /home/agent/.claude && chown -R agent:agent /home/agent/.claude
RUN mkdir -p /home/agent/.codex && chown -R agent:agent /home/agent/.codex

# Global CLAUDE.md — disables file-based memory, enforces MCP memory + skill usage
COPY claude-global.md /home/agent/.claude/CLAUDE.md
RUN chown agent:agent /home/agent/.claude/CLAUDE.md

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENV PYTHONPATH=/opt/agent
ENV PYTHONUNBUFFERED=1
# Headless matplotlib for the present_image tool (no display in container)
ENV MPLBACKEND=Agg
ENV MPLCONFIGDIR=/tmp/mpl

# entrypoint.sh runs as root (npm install -g needs it, the global prefix is
# root-owned) and drops to the unprivileged "agent" user itself via gosu
# before exec'ing the app -- so no Dockerfile-level USER switch here.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["python", "-m", "app.main"]
