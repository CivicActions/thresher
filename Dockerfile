FROM python:3.13-slim-bookworm

# System dependencies:
#  - libmagic1: MIME type detection (python-magic)
#  - libgl1, libglib2.0-0: OpenCV / image processing (docling)
#  - ffmpeg: audio/video format conversion (docling ASR)
#  - tesseract-ocr, libtesseract-dev, leptonica: OCR engine (docling)
#  - build-essential, pkg-config: native extension compilation (tree-sitter, etc)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
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
    p7zip-full

WORKDIR /app

# Install uv for fast dependency management (pinned to avoid cache busts)
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Install Python dependencies — changes only when pyproject.toml or uv.lock change.
# Project itself not installed; python -m thresher finds the package via WORKDIR.
# Uses CPU-only torch for smaller image size.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen --no-install-project --extra-index-url https://download.pytorch.org/whl/cpu

# Pre-download docling models so they're baked into the image.
# Uses venv binary directly so thresher source code isn't needed yet —
# this keeps the ~250s model download cached across code-only changes.
ENV HF_HOME=/app/.cache/huggingface
ENV TORCH_HOME=/app/.cache/torch
RUN .venv/bin/docling-tools models download

# Pre-download fastembed embedding models so they're baked into the image.
# Both models are downloaded here; only one is loaded at runtime per runner.
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('nomic-ai/nomic-embed-text-v1.5')"
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('jinaai/jina-embeddings-v2-base-code')"

# Pre-download tree-sitter grammars for code chunking.
# tree-sitter-language-pack downloads grammars lazily; without this
# they'd be missing at runtime in offline containers.
RUN .venv/bin/python -c "\
from tree_sitter_language_pack import download; \
download(['python','javascript','typescript','java','c','cpp','go','rust', \
  'ruby','php','csharp','swift','kotlin','scala','bash','perl','r','sql', \
  'lua','zig','elixir','erlang','haskell'])"

# Copy application code LAST — the most frequently changing layer.
# Nothing expensive rebuilds after this.
COPY thresher/ thresher/

# Prevent thread over-subscription in container environments
ENV OMP_NUM_THREADS=4
# Limit glibc malloc arenas to reduce RSS overhead
ENV MALLOC_ARENA_MAX=2
# Point tesserocr at Debian's tessdata (tesseract-ocr-eng package)
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# Entrypoint — use thresher CLI
ENTRYPOINT ["uv", "run", "python", "-m", "thresher"]

# Default to showing help
CMD ["--help"]
