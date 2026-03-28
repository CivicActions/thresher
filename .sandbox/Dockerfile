# Sandbox template for Thresher development
#
# Build on a machine with internet access (e.g. your local Docker Desktop):
#   docker build -t thresher-sandbox:latest .sandbox/
#
# Launch:
#   docker sandbox create --template thresher-sandbox:latest copilot-thresher .
#
# Alternative (capture running sandbox directly — no build needed):
#   docker sandbox save copilot-thresher thresher-sandbox:latest

FROM docker/sandbox-templates:copilot

USER root

# --- System packages ---
# libmagic-mgc + libmagic1t64: MIME detection for python-magic
# (Ubuntu 25.10 uses t64 transition package names)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic-mgc \
    libmagic1t64 \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# --- kubectl ---
ARG KUBECTL_VERSION=v1.32.3
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl" \
      -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl

# --- uv (latest — base image ships an older version) ---
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Place the project venv on the overlay filesystem (not the virtiofs-mounted
# workspace) so uv can hardlink from its cache. Without this, cross-device
# copies cause nvidia-cusparselt WHEEL tag mismatches and reinstall churn.
# The workspace .venv (if any) from the host is a different arch and must not
# be used inside the sandbox.
ENV UV_PROJECT_ENVIRONMENT=/home/agent/.venv

# --- Helper scripts ---
COPY --chmod=755 setup-ca-bundle.sh /usr/local/bin/setup-ca-bundle.sh
COPY --chmod=755 start-services.sh /usr/local/bin/start-services.sh
COPY --chmod=755 sandbox-init.sh /usr/local/bin/sandbox-init.sh

# --- Pre-download ML models ---
# Download HuggingFace tokenizer and fastembed ONNX model at image build time
# so they are available offline at runtime (no network needed during tests).
#
# Cache locations (default, no env override needed):
#   fastembed  → /tmp/fastembed_cache  (fastembed uses tempfile.gettempdir())
#   HF tokenizer → /home/agent/.cache/huggingface/hub
#
# Run as agent so the HF cache lands in the agent home dir.
# A temporary venv is used to install the download deps; it is deleted after
# the models are cached, keeping the image clean.
#
# Note: on the sandbox VM, onnxruntime exits with SIGILL (illegal instruction,
# exit 132) after successfully writing its first ONNX model to disk. This is a
# VM CPU-feature detection issue; the models ARE cached before the crash, so
# `|| true` is used to prevent the RUN step from failing.
COPY --chown=agent:agent download-models.py /tmp/download-models.py
USER agent
RUN uv venv /tmp/model-dl-env && \
    VIRTUAL_ENV=/tmp/model-dl-env uv pip install --quiet fastembed transformers && \
    (/tmp/model-dl-env/bin/python /tmp/download-models.py || true) && \
    rm -rf /tmp/model-dl-env /tmp/download-models.py
