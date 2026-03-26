# Quickstart: Thresher

**Spec**: [spec.md](spec.md) | **Branch**: `005-queue-runner-rebuild`

## Prerequisites

- Python 3.11+
- Docker (for container builds)
- `kubectl` configured (for K8s deployment)
- GCS bucket with document/source archive files
- Qdrant instance (local or remote)

## Local Development Setup

```bash
# Clone and checkout feature branch
git checkout 005-queue-runner-rebuild

# Install dependencies
pip install -e ".[dev]"
# Installs: docling, chonkie[code], fastembed, python-magic,
#           google-cloud-storage, qdrant-client, kubernetes, pyyaml

# Copy config template
cp config.example.yaml config.yaml
# Edit config.yaml with your GCS bucket and Qdrant settings
```

## Configuration

Create `config.yaml` (or set `THRESHER_CONFIG` env var):

```yaml
source:
  provider: gcs
  gcs:
    bucket: your-gcs-bucket

destination:
  provider: qdrant
  qdrant:
    url: http://localhost:6333

# Override/extend file type groups (optional — built-in defaults apply)
# file_type_groups:
#   custom-group:
#     extensions: [".custom"]
#     extractor: raw-text
#     chunker:
#       strategy: chonkie-recursive

routing:
  default_collection: vista
  rules:
    - name: source-code
      file_group: ["mumps-source", "general-source"]
      collection: vista-source
```

Environment variables override YAML:
```bash
export QDRANT_URL=http://qdrant.example.com:6333
export QDRANT_API_KEY=your-api-key
export GCS_BUCKET=your-bucket
```

## Running Locally

### Queue-only mode (build queue, don't process)

```bash
python -m thresher controller --config config.yaml
```

### Local mode (build queue + run embedded runner)

```bash
python -m thresher controller --config config.yaml --local
```

### Separate controller and runner

```bash
# Terminal 1: Build queue
python -m thresher controller --config config.yaml

# Terminal 2+: Run one or more runners
python -m thresher runner --config config.yaml --runner-id runner-01
python -m thresher runner --config config.yaml --runner-id runner-02
```

### Dry-run mode (report what would be processed)

```bash
python -m thresher controller --config config.yaml --dry-run
```

## Docker Build

```bash
docker build -t thresher:latest .

# Run controller locally in container
docker run --rm \
  -v $(pwd)/config.yaml:/etc/thresher/config.yaml \
  -e GOOGLE_APPLICATION_CREDENTIALS=/etc/gcloud/key.json \
  -v ~/.config/gcloud/application_default_credentials.json:/etc/gcloud/key.json \
  thresher:latest controller --config /etc/thresher/config.yaml --local
```

## Kubernetes Deployment

### Deploy controller that auto-creates runner Jobs

```bash
# Controller creates runner Jobs via K8s API
python -m thresher controller \
  --config config.yaml \
  --k8s-deploy
```

### Export runner manifests for review/GitOps

```bash
python -m thresher controller \
  --config config.yaml \
  --k8s-manifest-out runner-jobs.yaml

# Review and apply manually
kubectl apply -f runner-jobs.yaml
```

### CI Pipeline (typical)

```bash
# 1. Build and push image
docker build -t gcr.io/project/thresher:$TAG .
docker push gcr.io/project/thresher:$TAG

# 2. Deploy controller Job
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: thresher-controller
spec:
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: thresher-sa
      containers:
        - name: controller
          image: gcr.io/project/thresher:$TAG
          command: ["python", "-m", "thresher", "controller"]
          args: ["--config", "/etc/thresher/config.yaml", "--k8s-deploy"]
          volumeMounts:
            - name: config
              mountPath: /etc/thresher
          envFrom:
            - secretRef:
                name: thresher-secrets
      volumes:
        - name: config
          configMap:
            name: thresher-config
EOF
```

## Running Tests

```bash
# Unit tests
pytest tests/unit/

# Integration tests (requires GCS + Qdrant)
pytest tests/integration/

# Contract tests (provider interface conformance)
pytest tests/contract/
```

## Key CLI Commands

| Command | Description |
|---------|-------------|
| `thresher controller` | Scan files, expand archives, build queue |
| `thresher controller --local` | Build queue + run embedded runner |
| `thresher controller --k8s-deploy` | Build queue + create runner K8s Jobs |
| `thresher controller --k8s-manifest-out FILE` | Build queue + export runner manifests |
| `thresher controller --dry-run` | Report what would be processed |
| `thresher runner --runner-id ID` | Process files from queue |

## Architecture Summary

```
┌──────────────┐     scan/expand/partition      ┌─────────────┐
│  CI Pipeline │──── deploy ──────────────────►│  Controller  │
└──────────────┘                                │  (K8s Job)  │
                                                └──────┬──────┘
                                                       │ creates
                                          ┌────────────┼────────────┐
                                          ▼            ▼            ▼
                                    ┌──────────┐ ┌──────────┐ ┌──────────┐
                                    │ Runner 1 │ │ Runner 2 │ │ Runner N │
                                    │ (K8s Job)│ │ (K8s Job)│ │ (K8s Job)│
                                    └────┬─────┘ └────┬─────┘ └────┬─────┘
                                         │            │            │
                                    claim batch  claim batch  claim batch
                                         │            │            │
                                         ▼            ▼            ▼
                                    ┌──────────────────────────────────┐
                                    │     GCS Queue (batch files)      │
                                    │  pending/ → claimed/ → done/     │
                                    └──────────────────────────────────┘
                                         │
                                    classify → extract → chunk → embed
                                         │
                                         ▼
                                    ┌──────────────────────────────────┐
                                    │     Qdrant (vector collections)   │
                                    └──────────────────────────────────┘
```
