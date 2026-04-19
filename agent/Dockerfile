FROM python:3.12-slim AS base

# Version label - change this to trigger update detection for running agents
LABEL ai-employee.version="1.21.0"

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
    ripgrep \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally (pin version for reproducibility)
RUN npm install -g @anthropic-ai/claude-code@2.1.114

# Pre-install Playwright + Chromium for browser automation (Computer-Use Phase 1)
# Only activated when COMPUTER_USE_BROWSER=true — pre-installing avoids runtime download delays
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
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
    && npx -y playwright install chromium --with-deps 2>/dev/null || true

# Create non-root user with sudo installed (permissions configured dynamically by orchestrator)
# Container isolation is the real sandbox - sudo rules are set per-agent via permission packages
RUN useradd -m -s /bin/bash agent

# Install Python dependencies
WORKDIR /opt/agent
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app/ ./app/

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

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENV PYTHONPATH=/opt/agent
ENV PYTHONUNBUFFERED=1

# Run as non-root user
USER agent

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.main"]
