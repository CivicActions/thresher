# Thresher Docker Sandbox Template

Pre-configured development environment for the [Docker AI Sandbox](https://docs.docker.com/ai/sandboxes/).

## What's included

| Component | Purpose |
|-----------|---------|
| uv + Python 3 | Package management and runtime |
| commitizen | Conventional commit tooling |
| kubectl | Kubernetes CLI for k3s interaction |
| libmagic | MIME type detection (python-magic) |
| CA bundle fix | Combined certifi + proxy CA for `--bypass-host` compatibility |
| Service launcher | One-command startup for fake-gcs, Qdrant, k3s |
| ML model pre-download | HF tokenizer + fastembed ONNX cached before proxy is unset for K8s tests |

## Quick start

### Option A: Build template on host (recommended)

Build on your local machine where Docker Desktop has internet access:

```bash
docker build -t thresher-sandbox:latest .sandbox/
docker sandbox create --template thresher-sandbox:latest copilot-thresher .
```

### Option B: Save a running sandbox as template

If you already have a configured sandbox, capture it directly:

```bash
docker sandbox save copilot-thresher thresher-sandbox:latest
```

Then reuse it later:

```bash
docker sandbox create --template thresher-sandbox:latest copilot-thresher .
```

### After creating the sandbox

#### 1. Apply network policy

The sandbox proxy needs to bypass certain hosts for direct HTTPS (model downloads, container registry pulls). Apply the bypass rules while the sandbox is running:

```bash
docker sandbox network proxy copilot-thresher \
  --bypass-host "huggingface.co" \
  --bypass-host "*.huggingface.co" \
  --bypass-host "*.hf.co" \
  --bypass-host "registry-1.docker.io" \
  --bypass-host "*.docker.io" \
  --bypass-host "production.cloudflare.docker.com"
```

To check what's being blocked:

```bash
docker sandbox network log copilot-thresher
```

#### 2. Initialize inside the sandbox

Once the sandbox is running, run the init script **from the project directory**:

```bash
cd /path/to/thresher   # must be run from the project root
sandbox-init.sh
. /tmp/thresher-env.sh
```

This installs dependencies, starts test services, and configures the environment.

## Running tests

After initialization:

```bash
run-unit-tests          # ~500 unit tests (fast, no services needed)
run-functional-tests    # requires fake-gcs, qdrant, k3s services
run-all-tests           # everything
```

Or manually with proxy bypass for K8s tests:

```bash
HTTPS_PROXY= HTTP_PROXY= \
  SSL_CERT_FILE=/tmp/combined-ca-bundle.pem \
  KUBECONFIG=/tmp/k3s-kubeconfig.yaml \
  uv run pytest tests/ -v
```

## Known sandbox issues

### SSL certificate errors on bypassed hosts

**Symptom**: `SSLCertVerificationError: unable to get local issuer certificate` when accessing huggingface.co or similar bypassed hosts.

**Cause**: `SSL_CERT_FILE` is set to only the proxy CA cert. When `--bypass-host` causes a direct connection, the real server's certificate can't be verified against standard CAs.

**Fix**: Run `setup-ca-bundle.sh` (or `sandbox-init.sh` which calls it). This creates a combined bundle at `/tmp/combined-ca-bundle.pem` containing both certifi's root CAs and the proxy CA.

### onnxruntime SIGILL during model pre-download

**Symptom**: `sandbox-init.sh` exits with code 132 after model download, skipping service startup.

**Cause**: onnxruntime exits with SIGILL (illegal instruction) after successfully caching its ONNX model in the Docker sandbox VM. Models are cached before the crash, but `set -euo pipefail` treats exit 132 as fatal.

**Fix**: Already handled — `sandbox-init.sh` and the Dockerfile both use `|| true` on the download step. Rebuild the template to pick up the fix.

### nvidia-cusparselt reinstall churn on ARM64

**Symptom**: `uv run` outputs "Uninstalled 1 package / Installed 1 package" (nvidia-cusparselt-cu13) on every invocation, adding ~500ms overhead.

**Cause**: nvidia's aarch64 wheel declares `Tag: py3-none-manylinux2014_sbsa` but ARM64 systems only support `aarch64` tags. uv detects the mismatch and reinstalls every time.

**Fix**: `sandbox-init.sh` auto-patches the WHEEL tag after `uv sync`. To patch manually:

```bash
sed -i 's/manylinux2014_sbsa/manylinux2014_aarch64/' \
  .venv/lib/python3.13/site-packages/nvidia_cusparselt_cu13-0.8.0.dist-info/WHEEL
```

### K8s Python client ignores NO_PROXY

**Symptom**: `ProxyError: Tunnel connection failed: 502 Bad Gateway` when the K8s orchestrator talks to `127.0.0.1:6443`, or pipeline e2e tests skip with `Embedding model not available` (models can't be fetched with proxy unset).

**Cause**: The Python `kubernetes` client's urllib3 pool manager doesn't respect `NO_PROXY` for local addresses. The workaround (unsetting `HTTPS_PROXY`/`HTTP_PROXY`) also blocks direct connections to huggingface.co.

**Fix**: `sandbox-init.sh` pre-downloads ML models while the proxy is active, then sets `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` so tests use cached models. The `run-functional-tests` alias unsets proxy vars automatically.

## File reference

| File | Purpose |
|------|---------|
| `Dockerfile` | Sandbox template build definition |
| `download-models.py` | Pre-downloads ML models for offline test runs |
| `setup-ca-bundle.sh` | Creates combined CA bundle for proxy bypass |
| `start-services.sh` | Launches fake-gcs, Qdrant, and k3s containers |
| `sandbox-init.sh` | One-shot init: CA fix + deps + models + services |
| `proxy-config.json` | Reference network policy (use CLI commands to apply) |
