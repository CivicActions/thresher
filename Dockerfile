FROM python:3.13-slim

# System dependencies for docling, tree-sitter, etc
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY thresher/ thresher/

# Entrypoint — use thresher CLI
ENTRYPOINT ["uv", "run", "python", "-m", "thresher"]

# Default to showing help
CMD ["--help"]
