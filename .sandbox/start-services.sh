#!/usr/bin/env bash
# Start the functional test infrastructure services via Docker.
# Idempotent — skips containers that are already running.
#
# Services:
#   - fake-gcs-server (port 4443) — GCS emulator for source provider tests
#   - qdrant (ports 6333/6334)    — Vector DB for destination provider tests
#   - k3s (port 6443)             — Lightweight K8s for orchestrator tests

set -euo pipefail

COMPOSE_FILE="${1:-docker-compose.functional.yaml}"
PROJECT_DIR="${2:-/Users/owen.barton/workspace/thresher}"

echo "=== Starting functional test services ==="

# --- Docker Compose services (fake-gcs, qdrant) ---
if [ -f "$PROJECT_DIR/$COMPOSE_FILE" ]; then
    echo "Starting compose services from $COMPOSE_FILE..."
    docker compose -f "$PROJECT_DIR/$COMPOSE_FILE" up -d
else
    echo "WARNING: $COMPOSE_FILE not found, starting services manually..."
    docker run -d --name thresher-fake-gcs -p 4443:4443 \
        fsouza/fake-gcs-server:latest -scheme http -port 4443 -backend memory 2>/dev/null || true
    docker run -d --name thresher-qdrant -p 6333:6333 -p 6334:6334 \
        -e QDRANT__SERVICE__GRPC_PORT=6334 \
        qdrant/qdrant:latest 2>/dev/null || true
fi

# --- k3s (lightweight K8s) ---
if docker ps --format '{{.Names}}' | grep -q "^thresher-k3s$"; then
    echo "k3s already running"
else
    echo "Starting k3s..."
    docker run -d \
        --name thresher-k3s \
        --privileged \
        -p 6443:6443 \
        -e K3S_KUBECONFIG_OUTPUT=/output/kubeconfig.yaml \
        -e K3S_KUBECONFIG_MODE=644 \
        rancher/k3s:v1.32.3-k3s1 \
        server --disable=traefik --disable=metrics-server
fi

# --- Wait for k3s and extract kubeconfig ---
echo "Waiting for k3s API server..."
for i in $(seq 1 30); do
    if docker exec thresher-k3s kubectl get nodes &>/dev/null; then
        echo "k3s ready"
        break
    fi
    sleep 2
done

echo "Extracting kubeconfig..."
docker exec thresher-k3s cat /etc/rancher/k3s/k3s.yaml > /tmp/k3s-kubeconfig.yaml 2>/dev/null || true
export KUBECONFIG=/tmp/k3s-kubeconfig.yaml

# --- Verify ---
echo ""
echo "=== Service status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" \
    --filter "name=thresher-" 2>/dev/null || true

echo ""
echo "Set KUBECONFIG=/tmp/k3s-kubeconfig.yaml for kubectl access"
echo "Functional tests also need: unset HTTPS_PROXY HTTP_PROXY (for K8s client)"
