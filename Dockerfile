FROM python:3.13-slim-bookworm

# System dependencies:
#  - libmagic1: MIME type detection (python-magic)
#  - libgl1, libglib2.0-0: OpenCV / image processing (docling)
#  - ffmpeg: audio/video format conversion (docling ASR)
#  - tesseract-ocr, libtesseract-dev, leptonica: OCR engine (docling)
#  - build-essential, pkg-config: native extension compilation (tree-sitter, etc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    libtesseract-dev \
    libleptonica-dev \
    pkg-config \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files and install dependencies (project itself not installed —
# python -m thresher finds the package via WORKDIR)
# Uses CPU-only torch for smaller image size
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project --extra-index-url https://download.pytorch.org/whl/cpu

COPY thresher/ thresher/

# Pre-download docling models so they're baked into the image
ENV HF_HOME=/app/.cache/huggingface
ENV TORCH_HOME=/app/.cache/torch
RUN uv run docling-tools models download

# Prevent thread over-subscription in container environments
ENV OMP_NUM_THREADS=4
# Limit glibc malloc arenas to reduce RSS overhead
ENV MALLOC_ARENA_MAX=2

# Entrypoint — use thresher CLI
ENTRYPOINT ["uv", "run", "python", "-m", "thresher"]

# Default to showing help
CMD ["--help"]
