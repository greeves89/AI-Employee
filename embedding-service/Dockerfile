FROM python:3.12-slim

LABEL ai-employee.component="embedding-service"
LABEL ai-employee.version="1.0.0"

# System deps for sentence-transformers (torch has compiled binaries)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better caching).
# torch is installed from the CPU-only PyTorch index — the default PyPI wheel
# pulls ~2 GB of NVIDIA CUDA libraries this CPU-inference service never uses.
# CPU-only keeps the image ~2 GB smaller and the build dramatically faster.
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# Model is downloaded at runtime into the mounted /models volume on first start.
# This keeps the image small (~1 GB instead of 3.3 GB) and makes model changes easy.
# First startup takes 2-5 minutes to download bge-m3 (~2.3 GB).
ENV HF_HOME=/models
ENV TRANSFORMERS_CACHE=/models
ENV SENTENCE_TRANSFORMERS_HOME=/models
ENV MODEL_CACHE_DIR=/models

COPY app /app/app

EXPOSE 8001

# start-period covers the first-run model download (bge-m3 ~2.3 GB, 2-5 min);
# a shorter window marks the container unhealthy mid-download and restart-loops it
# before the download can finish (issue #287).
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8001/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
