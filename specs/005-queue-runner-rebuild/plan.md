# Implementation Plan: Thresher (Queue Runner Rebuild)

**Branch**: `005-queue-runner-rebuild` | **Date**: 2025-03-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-queue-runner-rebuild/spec.md`

## Summary

Thresher is a document processing pipeline that ingests archives of documents and source code, extracts content, chunks it intelligently, and indexes it into vector stores for semantic search. Built on a parallel queue runner architecture based on K8s Job patterns, the system splits into a **controller** (scans source files, expands archives, builds a pre-partitioned queue on GCS) and **runners** (claim batches, process one file at a time through classify → extract → chunk → index). File storage and vector indexing are abstracted behind **source provider** and **destination provider** interfaces (initially GCS and Qdrant). All configuration uses YAML with file type groups as first-class objects defining membership, extraction strategy, and chunking strategy. A single Docker image with dual entrypoints supports local, K8s-deployed, and manifest-export modes.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: docling (document extraction), chonkie[code] (tree-sitter AST chunking + recursive text chunking), fastembed (ONNX embeddings, sentence-transformers/all-MiniLM-L6-v2), python-magic (MIME detection), google-cloud-storage (GCS source provider), qdrant-client (Qdrant destination provider), kubernetes (K8s Job orchestration), PyYAML (configuration)
**Storage**: GCS (source provider — files, queue batches, caches, skip list), Qdrant (destination provider — vector collections with named vectors `fast-all-minilm-l6-v2`)
**Testing**: pytest (unit, integration, contract tests)
**Target Platform**: Linux containers on Kubernetes; local development on macOS/Linux
**Project Type**: Single CLI application (controller + runner entrypoints in same package)
**Performance Goals**: Process 500K+ files across parallel runner pods; each runner handles 100+ small files per pod lifecycle; full corpus run completes within comparable timeframe to current system at equal parallelism
**Constraints**: Runner RSS ≤ 4 GB per pod (configurable); per-file timeout 600s; docling subprocess isolation for native memory reclamation; `MALLOC_ARENA_MAX` tuning
**Scale/Scope**: 500K+ files; queue batches of 1000 items each; configurable runner parallelism capped at max setting

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Reliability First — ✅ PASS

| Principle | Spec Alignment |
|-----------|---------------|
| Idempotent & resumable | FR-003 (idempotent archive expansion), FR-005 (skip list), FR-019 (content-hash dedup), FR-016 (dual cache), queue claimed→done lifecycle |
| Failed files don't block others | FR-008 (one file at a time), FR-010 (mark complete/failed per item), FR-023/024 (retry + permanent failure) |
| Qdrant batch upserts with retry | FR-018 (index with metadata), FR-039 (destination provider interface — retry logic in provider) |
| Processing state recoverable | FR-021 (queue batches with state folders), FR-025 (lease timeout reclaims stale batches), FR-022 (attempt tracking) |

### II. Observability — ✅ PASS

| Principle | Spec Alignment |
|-----------|---------------|
| Progress reporting (processed, remaining, errors) | FR-031 (summary report with totals), FR-030 (structured logs) |
| Per-file logging (path, time, success/failure, errors) | FR-030 (structured logs with file paths, processing times, status, memory) |
| Qdrant batch logging (size, vectors, latency) | FR-039 (destination provider interface logs batch operations) |

### III. Simplicity — ✅ PASS

| Principle | Spec Alignment |
|-----------|---------------|
| K8s orchestration replaces custom parallelism | FR-042/043/044 (K8s Jobs manage concurrency — no custom thread pools, no concurrent.futures) |
| YAML config with env var overrides | FR-026/029/037 (3-layer merge: built-in defaults → user YAML → env vars) |
| Controller / runner dual entrypoint | FR-041 (single Docker image, two CLI entrypoints: `thresher controller`, `thresher runner`) |
| Minimal deps justified by spec requirements | chonkie[code] (FR-017 AST chunking), kubernetes (FR-042 Job orchestration), fastembed (FR-018) |

*Constitution v1.3.0 amended Principle III to reflect K8s orchestration, YAML config, and controller/runner architecture. All bullets now align directly.*

### IV. Lazy Processing — ✅ PASS

| Principle | Spec Alignment |
|-----------|---------------|
| Cache extracted markdown in cache directory | FR-016 (dual cache: `.md` markdown + `.docling.json` serialized document, both on source provider in cache path) |
| Cache separate from source | FR-016 (cache paths computed by source provider, not co-located with source files) |
| Skip extraction if cached | FR-016 (runner checks cache before invoking docling) |

### Error Handling — ✅ PASS

| Principle | Spec Alignment |
|-----------|---------------|
| Failed files logged to dedicated error file | FR-024 (permanently failed items in `queue/failed/` with error reason), SC-008 (100% error reasons) |
| Error logs include path, exception, timestamp | FR-030 (structured logs with full context) |
| Processing continues after failures | FR-008/010/011 (runner processes next item after failure) |
| Summary with total/succeeded/failed counts | FR-031 (summary report) |

## Project Structure

### Documentation (this feature)

```text
specs/005-queue-runner-rebuild/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── source-provider.md
│   ├── destination-provider.md
│   ├── config-schema.md
│   └── queue-batch.schema.json
├── tasks.md             # Phase 2 output (NOT created by /speckit.plan)
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
thresher/
├── __init__.py
├── __main__.py               # python -m thresher entrypoint
├── cli.py                    # CLI entrypoint (controller/runner modes)
├── config.py                 # YAML config loading (built-in defaults → user → env)
├── defaults.yaml             # Built-in file type group defaults (shipped in package)
├── controller/
│   ├── __init__.py
│   ├── scanner.py            # Source provider file scanning
│   ├── archive_expander.py   # Archive expansion (ZIP, TAR, GZ, etc.)
│   ├── queue_builder.py      # Queue partitioning and batch writing
│   └── k8s_orchestrator.py   # K8s Job creation / manifest export
├── runner/
│   ├── __init__.py
│   ├── loop.py               # Main runner loop (claim → process → mark)
│   ├── processor.py          # Single-file pipeline (classify → extract → chunk → index)
│   └── memory_monitor.py     # RSS monitoring and graceful exit
├── providers/
│   ├── __init__.py
│   ├── source.py             # Source provider protocol/ABC
│   ├── destination.py        # Destination provider protocol/ABC
│   ├── gcs.py                # GCS source provider implementation
│   └── qdrant.py             # Qdrant destination provider implementation
├── processing/
│   ├── __init__.py
│   ├── classifier.py         # File type group classification
│   ├── router.py             # Routing rules → collection mapping
│   ├── extractors/
│   │   ├── __init__.py
│   │   ├── docling.py        # Docling subprocess-isolated extraction
│   │   └── raw_text.py       # Raw text extraction
│   └── chunkers/
│       ├── __init__.py
│       ├── docling_hybrid.py # Docling HybridChunker wrapper
│       ├── chonkie_code.py   # Chonkie CodeChunker (tree-sitter)
│       ├── chonkie_recursive.py # Chonkie RecursiveChunker
│       └── mumps_label.py    # MUMPS label-boundary chunker
├── embedder.py               # FastEmbed ONNX embedding
├── types.py                  # Shared type definitions
├── url_resolver.py           # Source URL reconstruction
└── logging_config.py         # Structured logging setup

tests/
├── unit/
│   ├── test_config.py
│   ├── test_classifier.py
│   ├── test_router.py
│   ├── test_chunkers/
│   ├── test_extractors/
│   └── test_queue_builder.py
├── integration/
│   ├── test_gcs_provider.py
│   ├── test_qdrant_provider.py
│   └── test_runner_loop.py
└── contract/
    ├── test_source_provider.py
    └── test_destination_provider.py
```

**Structure Decision**: Single project with `thresher/` package, organized into `controller/`, `runner/`, `providers/`, and `processing/` subpackages. Old source files will be moved to a gitignored directory before new code is written (per spec assumptions). Tests mirror the source structure with unit/integration/contract separation. Contract tests validate provider interface conformance.

## Complexity Tracking

> Architectural decisions that add complexity beyond baseline. These align with Constitution v1.3.0 but are tracked here for visibility.

| Decision | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Provider abstraction (source + destination interfaces) | FR-038/039/040 require pluggable storage and indexing backends | Direct GCS/Qdrant calls were the original approach; spec explicitly requires interface abstraction for future S3, Weaviate, etc. |
| YAML config with file type groups | FR-026/029/037 require first-class config objects beyond simple env vars | TOML config was the original approach; spec explicitly requires YAML with hierarchical file type group definitions |
| kubernetes Python client dependency | FR-042/043/044 require programmatic K8s Job creation | Shell-out to kubectl rejected because controller needs structured Job spec construction and status feedback |
| chonkie[code] dependency (tree-sitter) | FR-017 requires AST-based code chunking for 165+ languages | Simple text splitting has no understanding of code structure; tree-sitter provides semantic boundaries |

## Constitution Check — Post-Design Re-evaluation

*Performed after Phase 1 design artifacts (data-model.md, contracts/, quickstart.md).*

**Result: ✅ All gates PASS. No new violations introduced.**

- **Reliability**: Provider interface contracts enforce idempotent operations (delete no-op on missing, upsert semantics, conditional writes). Queue batch schema tracks attempt counts and status transitions for full recoverability.
- **Observability**: ProcessingResult entity captures duration/status/errors/chunk_count. Config includes `summary_interval` for periodic progress.
- **Simplicity**: Provider protocols are minimal (7-8 methods). Config uses simple shallow merge (no deep merge library). No new abstractions beyond what spec requires.
- **Lazy Processing**: Source provider `cache_path()` method preserves dual-cache pattern. Data model explicitly documents `.md` and `.docling.json` cache paths.
