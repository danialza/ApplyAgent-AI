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
# - ca-certificates, libfontconfig1: tectonic (LaTeX compiler) deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl libgomp1 build-essential \
        ca-certificates libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Tectonic: a single-binary LaTeX compiler used by the tailored-CV
# renderer (POST /api/cv/render with compile_pdf=true). ~30 MB; pulls
# additional TeX packages on first compile and caches them.
ARG TECTONIC_VERSION=0.15.0
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
         amd64) TECTONIC_ARCH="x86_64-unknown-linux-musl" ;; \
         arm64) TECTONIC_ARCH="aarch64-unknown-linux-musl" ;; \
         *) echo "unsupported arch: $ARCH" && exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic@${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-${TECTONIC_ARCH}.tar.gz" \
       | tar -xz -C /usr/local/bin tectonic \
    && tectonic --version

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
