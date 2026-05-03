FROM python:3.11-slim AS runtime

# - PYTHONDONTWRITEBYTECODE: no .pyc clutter inside the image
# - PYTHONUNBUFFERED: stream uvicorn logs straight to docker
# - PIP_NO_CACHE_DIR: shrinks image
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/data/hf_cache

# OS deps:
# - curl: HEALTHCHECK
# - libgomp1, build-essential: needed by faiss-cpu / numpy / pdfplumber on slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl libgomp1 build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches when only app code changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy the rest of the backend.
COPY . .

# Run as a non-root user for safer container surface.
RUN useradd --uid 1000 --create-home --shell /bin/bash app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# `--proxy-headers` keeps client IPs / scheme correct when behind a reverse proxy.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
