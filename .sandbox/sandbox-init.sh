#!/usr/bin/env bash
# One-shot sandbox initialization script.
# Run this after creating a new sandbox to set up the full dev environment.
#
# Usage: sandbox-init.sh [project-dir]
#
# What it does:
#   1. Fixes the SSL CA bundle for proxy bypass compatibility
#   2. Installs Python project dependencies (uv sync)
#   3. Starts functional test services (fake-gcs, qdrant, k3s)
#   4. Writes environment exports to /tmp/thresher-env.sh

set -euo pipefail

PROJECT_DIR="${1:-/Users/owen.barton/workspace/thresher}"

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
if [ -f pyproject.toml ]; then
    uv sync
    echo "Dependencies installed"
else
    echo "WARNING: pyproject.toml not found in $PROJECT_DIR"
fi

# Install commitizen if not present
if ! command -v cz &>/dev/null; then
    echo "Installing commitizen..."
    uv tool install commitizen
fi

# --- Step 3: Start services ---
echo ""
echo "--- Step 3: Starting functional test services ---"
start-services.sh "$PROJECT_DIR/docker-compose.functional.yaml" "$PROJECT_DIR" || true

# --- Step 4: Write environment file ---
echo ""
echo "--- Step 4: Writing environment exports ---"
cat > /tmp/thresher-env.sh << 'ENVEOF'
# Source this file: . /tmp/thresher-env.sh

# CA bundle (proxy bypass fix)
if [ -f /tmp/combined-ca-bundle.pem ]; then
    export SSL_CERT_FILE=/tmp/combined-ca-bundle.pem
    export REQUESTS_CA_BUNDLE=/tmp/combined-ca-bundle.pem
fi

# K8s config
export KUBECONFIG=/tmp/k3s-kubeconfig.yaml

# For functional tests: Python K8s client doesn't respect NO_PROXY
# Unset proxy vars when running tests that talk to local k3s
alias run-functional-tests='HTTPS_PROXY= HTTP_PROXY= https_proxy= http_proxy= SSL_CERT_FILE=/tmp/combined-ca-bundle.pem REQUESTS_CA_BUNDLE=/tmp/combined-ca-bundle.pem KUBECONFIG=/tmp/k3s-kubeconfig.yaml uv run pytest tests/functional/ -v'
alias run-all-tests='HTTPS_PROXY= HTTP_PROXY= https_proxy= http_proxy= SSL_CERT_FILE=/tmp/combined-ca-bundle.pem REQUESTS_CA_BUNDLE=/tmp/combined-ca-bundle.pem KUBECONFIG=/tmp/k3s-kubeconfig.yaml uv run pytest tests/ -v'
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
