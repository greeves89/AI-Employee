FROM python:3.12-slim

LABEL ai-employee.component="stt-service"
LABEL ai-employee.version="1.0.0"

# ffmpeg: faster-whisper decodes the incoming OGG/Opus (Telegram) audio with it
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Whisper model is downloaded at runtime into the mounted /models volume on
# first start (the "small" model is ~480 MB). Keeps the image lean.
ENV HF_HOME=/models
ENV STT_MODEL=small

COPY app /app/app

EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8003/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
