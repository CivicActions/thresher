# Architecture

## Pipeline Overview

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│   Source     │     │    Controller    │     │   Queue     │
│   (GCS)     │────▶│                  │────▶│  (GCS JSON  │
│             │     │  1. scan direct  │     │   batches)  │
└─────────────┘     │  2. expand       │     └──────┬──────┘
                    │     archives     │            │
                    │  3. scan expanded│            ▼
                    │  4. build queue  │     ┌─────────────┐
                    └──────────────────┘     │   Runners   │
                                            │  claim batch,│
                    ┌──────────────────┐     │  process each│
                    │   Destination    │◀────│  file:       │
                    │   (Qdrant)      │     │   extract    │
                    │                 │     │   chunk      │
                    └──────────────────┘     │   embed      │
                                            │   index      │
                                            └─────────────┘
```

## Controller

The controller (`thresher/controller/`) orchestrates the pipeline in four phases:

1. **Scan direct files** — Lists source files, separates regular files from archives (zip, tar, gz, etc.), classifies by file type group, filters against skip list.
2. **Expand archives** — Expands archives in parallel (locally via `ThreadPoolExecutor` or as K8s Jobs — one per archive). Each archive's contents are uploaded to `expanded/` with concurrent batched uploads. Expansion records provide idempotency — re-runs skip already-expanded archives.
3. **Scan expanded files** — Lists the `expanded/` prefix to discover files produced by expansion.
4. **Build queue** — Merges direct and expanded items, partitions into batch JSON files written to `queue/pending/`.

**K8s Orchestrator** (optional) — Creates Kubernetes Jobs for both expansion and runner pods, or exports manifests for GitOps workflows.

The controller can also run an embedded runner (`--local` mode) for single-machine deployments.

## Archive Expansion

The `ExpansionOrchestrator` (`thresher/controller/expansion_orchestrator.py`) expands archives before the main processing phase:

**Local mode** — Archives expand in parallel using `ThreadPoolExecutor` (controlled by `max_expansion_parallelism`). Each archive is extracted, and its member files are uploaded to the `expanded/` prefix with concurrent batched uploads (`upload_batch_size`).

**K8s mode** — Each archive becomes a separate Kubernetes Job running the `thresher expander --archive-path <path>` CLI subcommand. Jobs execute in waves up to `max_expansion_parallelism` at a time.

**Idempotency** — Each successful expansion writes an `.expansion-record.json` file containing the archive path, member count, and timestamp. On re-run, archives with existing records are skipped.

**Failure handling** — A failed archive does not block others. Results aggregate success/failure counts, and failed archive paths are reported for retry or investigation.

## Runners

Each runner (`thresher/runner/`) is a stateless worker that:

1. **Claims** the next unclaimed batch (lease-based, with stale reclaim after timeout)
2. **Processes** each file: download → classify → extract → chunk → embed → index
3. **Reports** results (indexed / skipped / failed) and moves to the next batch
4. **Exits** if memory usage exceeds the configured threshold

In Kubernetes mode, multiple runner pods process batches in parallel. Each pod processes one file at a time to prevent memory accumulation.

## Processing Pipeline

For each file, the `FileProcessor` (`thresher/runner/processor.py`) runs:

```
Download → Classify → Extract → Chunk → Embed → Index
```

### Classify (`thresher/processing/classifier.py`)

Maps files to a `FileTypeGroup` by checking (in priority order):
- File extension (fast path)
- MIME type detection (via `python-magic`)
- Content detectors (custom functions, e.g. MUMPS label detection)

### Extract (`thresher/processing/extractors/`)

| Extractor | Used for | Notes |
|-----------|----------|-------|
| `raw-text` | Source code, plain text, data files | UTF-8/Latin-1/CP1252 decoding |
| `docling` | PDFs, Office docs, images, audio/video | Runs in subprocess for memory isolation |
| `skip` | Binary files | Returns immediately |

**Subprocess isolation**: Docling extraction spawns a child process via `posix_spawn` (not `fork`). This ensures native library memory (libpdfium, ONNX, PyTorch) is fully reclaimed by the OS after each file, preventing the memory leaks that motivated the architecture.

### Chunk (`thresher/processing/chunkers/`)

| Strategy | Used for | Notes |
|----------|----------|-------|
| `chonkie-recursive` | Text, data files | Semantic chunking with optional markdown recipe |
| `chonkie-code` | Source code | Tree-sitter AST-aware (165+ languages) |
| `docling-hybrid` | Office docs, PDFs | Preserves document heading hierarchy |
| `mumps-label-boundary` | MUMPS source | Respects MUMPS label definitions |

### Embed (`thresher/embedder.py`)

Generates vector embeddings using `sentence-transformers` (default: `all-MiniLM-L6-v2`, 384 dimensions). Models are pre-downloaded into the Docker image.

### Index (`thresher/providers/qdrant.py`)

Upserts chunks to Qdrant with deterministic point IDs (UUID5 from source path + chunk index) for idempotent writes. Each point carries metadata: source path, content hash, collection, chunk index, and reconstructed source URL.

## Provider Protocols

Storage and indexing are abstracted behind protocols, enabling new backends without changing pipeline code.

### SourceProvider (`thresher/providers/source.py`)

```python
class SourceProvider(Protocol):
    def list_files(self, prefix, recursive) -> Iterator[FileInfo]: ...
    def download_content(self, path) -> bytes: ...
    def upload_content(self, path, data, if_generation_match=None): ...
    def exists(self, path) -> bool: ...
    def delete(self, path): ...
```

Implementation: `GCSSourceProvider` (Google Cloud Storage).

### DestinationProvider (`thresher/providers/destination.py`)

```python
class DestinationProvider(Protocol):
    def ensure_collection(self, name, vector_size, vector_name): ...
    def index_chunks(self, collection, chunks: list[IndexChunk]): ...
    def exists_by_hash(self, collection, source_path, content_hash) -> bool: ...
    def delete_by_source(self, collection, source_path): ...
    def close(self): ...
```

Implementation: `QdrantDestinationProvider` (Qdrant vector DB).

## Extension Points

### Adding a new provider

1. Create a class implementing `SourceProvider` or `DestinationProvider` in `thresher/providers/`
2. Register it in the factory functions in `thresher/runner/processor.py`
3. Add configuration fields to `thresher/config.py` and `config_schema.json`

### Adding a new extractor

1. Create a function in `thresher/processing/extractors/` matching the signature `(path, content, config) -> str`
2. Wire it into `dispatch_extractor()` in `thresher/runner/processor.py`
3. Reference it by name in a file type group's `extractor` field

### Adding a new chunker

1. Create a function in `thresher/processing/chunkers/` returning `list[str]`
2. Wire it into `dispatch_chunker()` in `thresher/runner/processor.py`
3. Reference it by name in a file type group's `chunker.strategy` field

### Adding a new content detector

1. Add a detector function to `thresher/processing/classifier.py`
2. Register it in the `DETECTORS` dict
3. Reference it by name in a file type group's `detectors` list

## Configuration System

Configuration is loaded via a three-layer merge:

1. **Built-in defaults** (`thresher/defaults.yaml`) — standard file type groups and sensible defaults
2. **User YAML** (`--config`) — overrides defaults; same-name file type groups replace entirely
3. **Environment variables** — `GCS_BUCKET`, `QDRANT_URL`, `QDRANT_API_KEY`

The schema is validated on load against `thresher/config_schema.json`.

See [`config.example.yaml`](../config.example.yaml) for the full annotated template and [`specs/`](../specs/) for detailed design specifications and provider contracts.
