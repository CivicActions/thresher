# Thresher

Cloud-native pipeline for converting documents, source code, images, audio/video, and archives into chunked markdown indexed in a vector search backend. Designed for scale: subprocess-isolated extraction prevents memory leaks, Kubernetes-native queue/runner architecture enables horizontal scaling, and flexible YAML configuration avoids hardcoding.

## Features

- **Multi-format**: Office docs, PDFs, images, audio/video, source code, archives, and plain text
- **Configurable**: YAML-driven file type groups, routing rules, and processing strategies
- **Extensible**: Protocol-based providers — swap storage backends, vector DBs, extractors, and chunkers
- **Resilient**: Subprocess isolation, retry logic, lease-based queue claiming, memory-aware runners
- **Kubernetes-native**: Controller builds queue batches, deploys runner Jobs; runners auto-scale
- **Incremental**: Skip lists track completed files; re-runs process only new/changed content

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/your-org/thresher.git
cd thresher
uv sync

# 2. Configure (copy and edit)
cp config.example.yaml config.yaml
# Set at minimum: source.gcs.bucket, destination.qdrant.url

# 3. Run locally (controller + embedded runner)
uv run thresher controller --config config.yaml --local

# Or with Docker
docker build -t thresher:latest .
docker run -v $(pwd)/config.yaml:/config.yaml \
  -e GCS_BUCKET=my-bucket \
  -e QDRANT_URL=http://qdrant:6333 \
  thresher:latest controller --config /config.yaml --local
```

## CLI

### Controller — scan, classify, build queue

```bash
thresher controller --config config.yaml [OPTIONS]

Options:
  --local                Run an embedded runner after building the queue
  --k8s-deploy           Deploy runner K8s Jobs
  --k8s-manifest-out F   Export Job manifests to file (for GitOps)
  --dry-run              Report file counts without processing
  --force                Reprocess all files (ignore skip list)
```

### Runner — claim batches, process files

```bash
thresher runner --config config.yaml --runner-id runner-001 [OPTIONS]

Options:
  --force    Force reprocess all claimed files
```

## Configuration

Configuration merges three layers: built-in defaults → YAML config → environment variables.

| Section | Purpose | Key settings |
|---------|---------|-------------|
| `source` | File storage provider | `gcs.bucket`, prefixes for source/expanded/cache/queue |
| `destination` | Vector store | `qdrant.url`, `qdrant.api_key`, batch size |
| `file_type_groups` | File classification & processing | Extensions, MIME types, extractor, chunker strategy, max size |
| `routing` | File → collection mapping | Rules with path/filename/file-group matchers, default collection |
| `embedding` | Vector embedding model | Model name, vector size, max tokens |
| `processing` | Timeouts, retries, memory limits | `per_file_timeout`, `retry_max`, `memory_threshold_mb` |
| `queue` | Batch sizing and lease management | `batch_size`, `lease_timeout` |
| `kubernetes` | Runner Job configuration | Image, resources, parallelism, tolerations |
| `url_resolvers` | Source URL reconstruction | httrack, regex pattern, domain-first resolvers |

Environment variables: `GCS_BUCKET`, `QDRANT_URL`, `QDRANT_API_KEY`.

See [`config.example.yaml`](config.example.yaml) for the full template with comments and [`thresher/defaults.yaml`](thresher/defaults.yaml) for built-in file type groups.

## Docker

```bash
# Build (pre-downloads ML models into image)
docker build -t thresher:latest .

# Controller + local runner
docker run \
  -e GCS_BUCKET=my-bucket \
  -e QDRANT_URL=http://qdrant:6333 \
  thresher:latest controller --local

# Standalone runner
docker run \
  -e QDRANT_URL=http://qdrant:6333 \
  thresher:latest runner --runner-id runner-001
```

## Testing

```bash
# Unit tests (~470 tests)
uv run pytest tests/unit/ -v

# Functional tests (requires Docker services)
docker compose -f docker-compose.functional.yaml up -d
uv run pytest tests/functional/ -v

# All tests
uv run pytest tests/ -v

# Lint
uv run ruff check .
uv run ruff format --check .
```

## Project Layout

```
thresher/
├── controller/     # Scanner, archive expander, queue builder, K8s orchestrator
├── runner/         # Processing loop, file processor, memory monitor
├── providers/      # Source (GCS) and destination (Qdrant) abstractions
├── processing/     # Classifier, router, extractors, chunkers
├── cli.py          # CLI entry point
├── config.py       # Three-layer configuration loading
├── embedder.py     # Vector embedding (sentence-transformers)
└── types.py        # Core data types
```

## Documentation

- [Architecture overview](docs/architecture.md) — pipeline design, extension points
- [Configuration template](config.example.yaml) — annotated YAML with all options
- [Contributing guide](CONTRIBUTING.md) — development setup, conventions
- [Design specs](specs/) — detailed specifications and contracts

## License

[GNU Affero General Public License v3.0](LICENSE)
