FROM python:3.12-slim

LABEL ai-employee.component="tts-service"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV HF_HOME=/models
ENV TRANSFORMERS_CACHE=/models
ENV TTS_MODEL=microsoft/VibeVoice-1.5B

COPY app /app/app

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -f http://localhost:8002/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
