#!/usr/bin/env bash
# One-shot sandbox initialization script.
# Run this after creating a new sandbox to set up the full dev environment.
#
# Usage: sandbox-init.sh [project-dir]
#
# What it does:
#   1. Fixes the SSL CA bundle for proxy bypass compatibility
#   2. Installs Python project dependencies (uv sync)
#   3. Pre-downloads ML models (HF tokenizer + fastembed ONNX) while proxy is active
#   4. Starts functional test services (fake-gcs, qdrant, k3s)
#   5. Writes environment exports to /tmp/thresher-env.sh

set -euo pipefail

# Auto-detect project dir: use argument, or find the mounted workspace by
# looking for pyproject.toml starting from cwd up to known sandbox mount points.
_detect_project_dir() {
    local dir="$PWD"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/pyproject.toml" ]]; then
            echo "$dir"
            return
        fi
        dir="$(dirname "$dir")"
    done
    # Fallback: check common sandbox mount locations
    for candidate in /workspace /home/user/workspace /root/workspace; do
        if [[ -f "$candidate/pyproject.toml" ]]; then
            echo "$candidate"
            return
        fi
    done
    # Last resort: current directory
    echo "$PWD"
}
PROJECT_DIR="${1:-$(_detect_project_dir)}"

echo "========================================"
echo " Thresher Sandbox Init"
echo "========================================"
echo ""

# --- Step 1: Fix CA bundle ---
echo "--- Step 1: CA bundle setup ---"
setup-ca-bundle.sh || true

# --- Step 2: Install project dependencies ---
echo ""
echo "--- Step 2: Installing project dependencies ---"
cd "$PROJECT_DIR"

# Ensure venv lives on overlay fs (same as uv cache) — avoids cross-device
# hardlink failures and nvidia-cusparselt WHEEL tag mismatch churn.
# Also prevents the sandbox from using a host .venv with the wrong arch.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-/home/agent/.venv}"

if [ -f pyproject.toml ]; then
    uv sync
    echo "Dependencies installed (venv: $UV_PROJECT_ENVIRONMENT)"
    # Workaround: nvidia-cusparselt-cu13 wheel declares Tag: py3-none-manylinux2014_sbsa
    # but ARM64 systems only support aarch64 tags. This nvidia packaging bug causes uv to
    # detect a platform mismatch and reinstall the package on every invocation.
    CUSPARSELT_WHEEL="$UV_PROJECT_ENVIRONMENT/lib/python3.13/site-packages/nvidia_cusparselt_cu13-0.8.0.dist-info/WHEEL"
    if [ -f "$CUSPARSELT_WHEEL" ] && grep -q manylinux2014_sbsa "$CUSPARSELT_WHEEL"; then
        sed -i 's/manylinux2014_sbsa/manylinux2014_aarch64/' "$CUSPARSELT_WHEEL"
        echo "Patched nvidia-cusparselt WHEEL tag (sbsa→aarch64)"
    fi
else
    echo "WARNING: pyproject.toml not found in $PROJECT_DIR"
fi

# Install commitizen if not present
if ! command -v cz &>/dev/null; then
    echo "Installing commitizen..."
    uv tool install commitizen
fi

# --- Step 3: Pre-download ML models ---
# The chunker (chonkie) needs the sentence-transformers tokenizer from HuggingFace,
# and the embedder needs the fastembed ONNX model. Both are pre-baked into the
# sandbox image (see Dockerfile), so this step is a no-op for image-based sandboxes.
# It runs the download only when models are missing — e.g. a sandbox created via
# `docker sandbox save` from a running container that skipped the build step.
echo ""
echo "--- Step 3: Pre-downloading ML models ---"
FASTEMBED_CACHE="/tmp/fastembed_cache"
HF_TOKENIZER_CACHE="${HOME}/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2"
# Check for actual model files, not just directory existence — an empty directory
# (e.g. created by a prior failed download) must not suppress the download step.
_fastembed_has_models() { find "$FASTEMBED_CACHE" -name "*.onnx" -type f 2>/dev/null | grep -q .; }
if _fastembed_has_models && [ -d "$HF_TOKENIZER_CACHE" ]; then
    echo "  Models already cached (pre-baked in image) — skipping download"
elif [ -f pyproject.toml ]; then
    echo "  Models not found in cache — downloading now..."
    # NOTE: onnxruntime exits with SIGILL (132) after loading its first model in the
    # Docker sandbox VM — the models ARE cached successfully before the crash, but
    # set -euo pipefail would abort this script without || true.
    SSL_CERT_FILE=/tmp/combined-ca-bundle.pem \
    REQUESTS_CA_BUNDLE=/tmp/combined-ca-bundle.pem \
    uv run python - << 'PYEOF' || true
import sys
try:
    from transformers import AutoTokenizer
    AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
    print("  tokenizer: sentence-transformers/all-MiniLM-L6-v2 cached")
except Exception as e:
    print(f"  WARNING: tokenizer download failed: {e}", file=sys.stderr)

try:
    from fastembed import TextEmbedding
    TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    print("  fastembed: sentence-transformers/all-MiniLM-L6-v2 cached")
except Exception as e:
    print(f"  WARNING: fastembed model download failed: {e}", file=sys.stderr)
PYEOF
else
    echo "WARNING: skipping model pre-download (no pyproject.toml)"
fi

# --- Step 4: Start services ---
echo ""
echo "--- Step 4: Starting functional test services ---"
start-services.sh "docker-compose.functional.yaml" "$PROJECT_DIR" || true

# --- Step 5: Write environment file ---
echo ""
echo "--- Step 5: Writing environment exports ---"
cat > /tmp/thresher-env.sh << 'ENVEOF'
# Source this file: . /tmp/thresher-env.sh

# Venv on overlay fs — avoids cross-device hardlink failures and arch mismatch
# with any host .venv mounted via virtiofs.
export UV_PROJECT_ENVIRONMENT=/home/agent/.venv

# CA bundle (proxy bypass fix)
if [ -f /tmp/combined-ca-bundle.pem ]; then
    export SSL_CERT_FILE=/tmp/combined-ca-bundle.pem
    export REQUESTS_CA_BUNDLE=/tmp/combined-ca-bundle.pem
fi

# K8s config
export KUBECONFIG=/tmp/k3s-kubeconfig.yaml

# Use cached ML models — proxy is unset for K8s tests (K8s client ignores NO_PROXY),
# so HuggingFace must not attempt network requests during test runs.
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

# For functional tests: Python K8s client doesn't respect NO_PROXY
# Unset proxy vars when running tests that talk to local k3s
alias run-functional-tests='HTTPS_PROXY= HTTP_PROXY= https_proxy= http_proxy= SSL_CERT_FILE=/tmp/combined-ca-bundle.pem REQUESTS_CA_BUNDLE=/tmp/combined-ca-bundle.pem KUBECONFIG=/tmp/k3s-kubeconfig.yaml HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run pytest tests/functional/ -v'
alias run-all-tests='HTTPS_PROXY= HTTP_PROXY= https_proxy= http_proxy= SSL_CERT_FILE=/tmp/combined-ca-bundle.pem REQUESTS_CA_BUNDLE=/tmp/combined-ca-bundle.pem KUBECONFIG=/tmp/k3s-kubeconfig.yaml HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run pytest tests/ -v'
alias run-unit-tests='uv run pytest tests/unit/ -v'
ENVEOF

echo "Environment written to /tmp/thresher-env.sh"
echo ""
echo "========================================"
echo " Setup complete!"
echo ""
echo " Quick start:"
echo "   . /tmp/thresher-env.sh"
echo "   run-unit-tests          # 500+ unit tests"
echo "   run-functional-tests    # requires services"
echo "   run-all-tests           # everything"
echo "========================================"
