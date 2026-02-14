FROM python:3.12-slim AS base

# Install system dependencies + Node.js (required for Claude Code CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    sudo \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user with passwordless sudo (can install system packages)
RUN useradd -m -s /bin/bash agent \
    && echo "agent ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/agent

# Install Python dependencies
WORKDIR /opt/agent
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app/ ./app/

# Install ai-team CLI helper (allows agents to call orchestrator API)
COPY scripts/ai-team /usr/local/bin/ai-team
RUN chmod +x /usr/local/bin/ai-team

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

CMD ["python", "-m", "app.main"]
