# Thresher — Copilot Instructions

Cloud-native pipeline that converts documents (Office, PDF, images, audio/video, source code, archives) into chunked markdown indexed in a vector search backend.

## Commands

```bash
# Dependencies
uv sync --dev

# Unit tests (~500, fast)
uv run pytest tests/unit/ -v

# Single test file
uv run pytest tests/unit/test_config.py -v

# Single test
uv run pytest tests/unit/test_config.py::TestLoadDefaults::test_returns_config_instance -v

# Functional tests (requires Docker services running)
docker compose -f docker-compose.functional.yaml up -d
uv run pytest tests/functional/ -v

# Lint + format + type check (all pre-commit checks)
uv run prek

# Lint only
uv run ruff check .
uv run ruff check . --fix    # auto-fix

# Format
uv run ruff format .

# Type check
uv run ty check

# Version bump (conventional commits)
uv run cz bump
```

## Architecture

**Controller → Queue → Runners** pipeline with four controller phases:

1. **Scan direct files** — `scanner.scan_direct_files()` returns `(items, archives)` tuple
2. **Expand archives** — `ExpansionOrchestrator` expands in parallel (local ThreadPoolExecutor or K8s Jobs, one per archive). Writes `.expansion-record.json` for idempotency.
3. **Scan expanded files** — `scanner.scan_expanded_files()` discovers extracted content
4. **Build queue** — Merges items into batch JSON files in `queue/pending/`

**Runners** are stateless workers that claim batches and process each file through:
```
Download → Classify → Extract → Chunk → Embed → Index
```

Extraction runs in subprocess isolation (`posix_spawn`, not `fork`) so native library memory (libpdfium, ONNX, PyTorch) is fully reclaimed by the OS after each file.

**CLI subcommands**: `controller` (scan/queue/deploy), `runner` (process batches), `expander` (expand single archive, used by K8s Jobs).

## Provider Protocols

Storage and indexing are abstracted behind `Protocol` classes. Implement these to add new backends:

- **`SourceProvider`** (`thresher/providers/source.py`): `list_files`, `download_content`, `upload_content`, `exists`, `delete`. Implementation: `GCSSourceProvider`.
- **`DestinationProvider`** (`thresher/providers/destination.py`): `ensure_collection`, `index_chunks`, `exists_by_hash`, `delete_by_source`, `close`. Implementation: `QdrantDestinationProvider`.

Register new providers in factory functions in `thresher/runner/processor.py`.

## Adding Extractors, Chunkers, Detectors

- **Extractor**: Add function in `thresher/processing/extractors/`, wire into `dispatch_extractor()` in `processor.py`, reference by name in a file type group's `extractor` field.
- **Chunker**: Add function in `thresher/processing/chunkers/`, wire into `dispatch_chunker()`, reference as `chunker.strategy`.
- **Detector**: Add function to `thresher/processing/classifier.py`, register in `DETECTORS` dict, reference in file type group's `detectors` list.

Current extractors: `raw-text`, `docling`, `skip`. Chunkers: `chonkie-recursive`, `chonkie-code`, `docling-hybrid`, `mumps-label-boundary`.

## Configuration

Three-layer merge: `thresher/defaults.yaml` → user YAML (`--config`) → env vars (`GCS_BUCKET`, `QDRANT_URL`, `QDRANT_API_KEY`). Schema validated against `thresher/config_schema.json`.

File type groups define the processing pipeline per file type: extensions, MIME types, extractor, chunker strategy, and max file size. User groups with the same name completely replace the built-in group.

## Key Conventions

- **Ruff**: line length 100, rules E/F/I/W. Type hints required on all public functions. Docstrings required on classes and public methods.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`), enforced by commitizen.
- **Tests**: pytest with class-based organization. Markers: `@pytest.mark.functional` (needs Docker services), `@pytest.mark.integration`, `@pytest.mark.contract`. Mocks via `unittest.mock`.
- **Idempotency**: Point IDs are deterministic UUID5 from `(source_path, chunk_index)`. Expansion records prevent re-expansion. Skip lists track processed files.
- **Pre-commit**: Run `uv run prek` before committing — runs ruff check, ruff format, and ty.

## Core Types

Defined in `thresher/types.py`: `FileInfo`, `FileTypeGroup`, `ChunkerConfig`, `RoutingRule`, `QueueItem`, `QueueBatch`, `ProcessingResult`, `IndexChunk`, `ExpansionResult`, `ExpansionRecord`. Point IDs use `make_point_id(source_path, chunk_index)` with a project-specific UUID namespace.

## Docker Sandbox

Development environment often uses [Docker AI Sandbox](https://docs.docker.com/ai/sandboxes/). If you are running inside a Docker sandbox (check: `[ -f /usr/local/share/ca-certificates/proxy-ca.crt ]`), read `.sandbox/README.md` for environment setup, known issues, and workarounds. Key points:

- Run `sandbox-init.sh && . /tmp/thresher-env.sh` to initialize (CA bundle, deps, services, env vars)
- The venv is at `/home/agent/.venv` (overlay fs), **not** `.venv` in the project dir
- Use `run-unit-tests`, `run-functional-tests`, `run-all-tests` aliases after sourcing the env file
- Functional tests need `HTTPS_PROXY= HTTP_PROXY=` unset (K8s client bug) and `HF_HUB_OFFLINE=1` (models pre-cached)
