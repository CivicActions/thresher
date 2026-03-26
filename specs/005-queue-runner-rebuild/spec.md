# Feature Specification: Thresher (Queue Runner Rebuild)

**Feature Branch**: `005-queue-runner-rebuild`  
**Created**: 2025-03-25  
**Status**: Draft  
**Input**: User description: "Redesign and rebuild the application from scratch using a parallel queue runner architecture based on k8s job patterns, with a controller/runner split, failure resilience, configurable collection routing, and generalized file type handling."
**Project Name**: Thresher — a document processing pipeline that separates valuable content from noise.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Full Pipeline Run with Queue Architecture (Priority: P1)

An operator launches the pipeline against a GCS bucket containing VistA/RPMS documentation and source code. The controller scans all source files, expands archives (storing extracted files back to a separate GCS folder), and builds a queue of individual files to process. Multiple runner pods each pick up one file at a time from the queue, download it, run it through classification and docling extraction/chunking, index the results to Qdrant, and mark the queue item as complete before picking up the next file. No parallel processing happens within a single runner — Kubernetes handles parallelism by scaling the number of runner pods.

**Why this priority**: This is the core architectural change that resolves the memory leak and OOM issues. Without the controller/runner split and single-file-at-a-time processing, the fundamental problems remain.

**Independent Test**: Can be tested end-to-end by running the controller to build a queue from a small GCS prefix, then running a single runner instance that processes all queued items sequentially. Verify that files are classified, extracted, chunked, and indexed in Qdrant with correct metadata.

**Acceptance Scenarios**:

1. **Given** a GCS bucket with source files and archives, **When** the controller is launched, **Then** it scans all blobs, expands archives to a separate GCS folder, and creates a queue entry for each individual processable file.
2. **Given** a populated queue, **When** a runner starts, **Then** it picks up one file, downloads it, processes it (classify → extract → chunk → index), marks it complete, and picks up the next file.
3. **Given** a runner processing files, **When** it finishes all available queue items, **Then** it exits cleanly with a summary of processed/skipped/failed counts.
4. **Given** multiple runner pods running concurrently, **When** they access the queue, **Then** each file is claimed by exactly one runner with no duplicate processing.

---

### User Story 2 - Failure Resilience and Retry (Priority: P1)

A runner encounters an OOM kill or crash while processing a particularly large or malformed file. The queue system detects that the item was not marked complete, and it becomes available for retry. After a configurable number of retries (default: 3), the file is moved to a permanent failure state so it does not block future runs. Operators can inspect which files failed and why.

**Why this priority**: Resilience to OOM and crashes is the primary motivation for this rebuild. The system must gracefully handle the failure modes that plague the current architecture.

**Independent Test**: Can be tested by simulating a runner crash (e.g., killing the process mid-file) and verifying that the item returns to the queue and is picked up by another runner. After 3 failures, verify it moves to a permanent failure state.

**Acceptance Scenarios**:

1. **Given** a runner crashes while processing a file, **When** the item's lease/lock expires, **Then** the file becomes available in the queue for another runner to pick up.
2. **Given** a file has failed processing 3 times, **When** a runner encounters it in the queue, **Then** the file is marked as permanently failed and skipped.
3. **Given** a completed pipeline run, **When** the operator reviews results, **Then** a clear report shows which files succeeded, which failed permanently, and the error reasons.
4. **Given** a runner that has been running for a long time, **When** it detects its own memory usage exceeding a threshold, **Then** it finishes the current file and exits gracefully (allowing k8s to restart it fresh).

---

### User Story 3 - Configurable Collection Routing (Priority: P2)

An operator configures the pipeline with rules that route files to different Qdrant collections based on file type groups and path patterns. For example, MUMPS source code under an "rpms" directory goes to the "rpms-source" collection, while PDF documentation under "vista" goes to the "vista" collection. File type groups define membership via extensions, MIME types, and custom detectors — and also specify how files in that group are extracted and chunked. Routing rules reference these groups alongside path, partial path, and filename patterns. All configuration uses YAML, with a built-in defaults file providing standard file type groups that users can extend or override.

**Why this priority**: Generalizing the routing makes the system adaptable to different use cases and removes brittle hard-coded assumptions, but the core pipeline must work first.

**Independent Test**: Can be tested by configuring routing rules in the YAML config file, running a small set of files through the pipeline, and verifying each file lands in the correct Qdrant collection based on its file type group and path.

**Acceptance Scenarios**:

1. **Given** routing rules mapping file type groups and path patterns to collections, **When** a file is processed, **Then** it is indexed into the collection matching the first applicable rule.
2. **Given** a file that matches no routing rules, **When** it is processed, **Then** it is indexed into the configured default collection.
3. **Given** a new project with different directory structures, **When** an operator creates routing rules and custom file type groups in configuration, **Then** files are routed correctly without any code changes.
4. **Given** a user defines a file type group with the same name as a built-in group, **When** configuration is loaded, **Then** the user definition completely replaces the built-in; unmentioned built-in groups are preserved.

---

### User Story 4 - Archive Expansion to GCS (Priority: P2)

The controller encounters ZIP and TAR archives in the source bucket. It downloads each archive, extracts the individual files, and uploads them to a separate top-level folder in GCS (e.g., `expanded/`). These expanded files are then queued as individual items for runners to process. This decouples archive handling from document processing, ensuring each runner only ever deals with single files.

**Why this priority**: Archive expansion is essential to the controller/runner split — runners should never need to handle archives directly, which simplifies their memory profile.

**Independent Test**: Can be tested by placing archives in a GCS bucket, running the controller, and verifying that individual files appear in the `expanded/` GCS folder and are queued for processing.

**Acceptance Scenarios**:

1. **Given** a ZIP archive in the source bucket, **When** the controller processes it, **Then** all non-binary member files are extracted and uploaded to the expanded files GCS folder.
2. **Given** a nested archive (archive within archive), **When** the controller processes it, **Then** inner archives are also expanded recursively up to a configurable depth.
3. **Given** an archive that has already been expanded in a previous run, **When** the controller runs again, **Then** it skips re-expansion (idempotent behavior).

---

### User Story 5 - Skip List and Incremental Processing (Priority: P2)

An operator re-runs the pipeline after a partial failure or after new files are added. The system consults a skip list (stored in GCS) and the queue's completion records to avoid re-processing files that were already successfully indexed. The operator can override this with a force flag to re-process everything.

**Why this priority**: Incremental processing is critical for operational efficiency — full re-runs of the entire corpus are expensive and slow.

**Independent Test**: Can be tested by running the pipeline once, adding new files, re-running, and verifying only new files are processed. Then re-running with force flag and verifying all files are reprocessed.

**Acceptance Scenarios**:

1. **Given** a previous successful run, **When** the controller builds the queue for a new run, **Then** files already in the skip list are excluded from the queue.
2. **Given** a force flag is set, **When** the controller builds the queue, **Then** all files are included regardless of previous completion status.
3. **Given** a file that was updated in GCS since last processing, **When** the controller builds the queue, **Then** the updated file is included for reprocessing.

---

### User Story 6 - Extensible Chunking Strategy (Priority: P3)

The system uses four chunking strategies, each assigned to file type groups via configuration: MUMPS-aware label-boundary chunking for MUMPS source files, Docling's HybridChunker for docling-extracted documents, Chonkie's CodeChunker (tree-sitter AST-based, 165+ languages) for non-MUMPS source code, and Chonkie's RecursiveChunker for plain text and markdown. The chunking strategy is specified per file type group in the YAML config and is designed to be extensible — new chunkers can be added for additional file types by defining a new file type group with the appropriate chunker strategy.

**Why this priority**: The current chunking strategies work well but the dispatch is hardcoded. This story makes them config-driven and adds a better code chunker.

**Independent Test**: Can be tested by processing files of different types (MUMPS, PDF, Python source, plain text) and verifying each uses the appropriate chunking strategy with correct output format.

**Acceptance Scenarios**:

1. **Given** a MUMPS source file, **When** processed by a runner, **Then** it is chunked using MUMPS-aware label-boundary chunking as specified by its file type group.
2. **Given** a PDF document, **When** processed by a runner, **Then** it is extracted via docling and chunked using the docling-hybrid chunker.
3. **Given** a Python source file, **When** processed by a runner, **Then** it is chunked using the chonkie-code strategy (tree-sitter AST-based).
4. **Given** a plain text or markdown file, **When** processed by a runner, **Then** it is chunked using the chonkie-recursive strategy.
5. **Given** a new file type requiring a specialized chunker, **When** an operator defines a new file type group with the appropriate chunker strategy in configuration, **Then** files of that type are routed to the new chunker without modifying existing pipeline code.

---

### User Story 7 - Docker Image and K8s Job Orchestration (Priority: P2)

A CI pipeline (e.g., GitLab CI) builds and pushes a single Docker image containing both controller and runner entrypoints. The CI pipeline then deploys a controller K8s Job. The controller scans the source files, builds the queue, and uses the K8s API to programmatically create runner Jobs — one per batch or a configurable number of parallel runner pods. The controller determines the appropriate parallelism based on the number of queue batches. Runners process their batches and exit. The controller can optionally output the generated K8s Job manifests to a file (for inspection, custom deployment, or GitOps workflows) instead of applying them directly.

**Why this priority**: Automating the full CI-to-processing pipeline is essential for operational use. The controller must be able to self-orchestrate runner Jobs without manual intervention.

**Independent Test**: Can be tested by running the controller with `--k8s-deploy` against a test cluster, verifying it creates the correct number of runner Jobs with proper image, resource limits, environment variables, and config mounts. Test manifest export with `--k8s-manifest-out`.

**Acceptance Scenarios**:

1. **Given** a CI pipeline that builds and pushes the Docker image, **When** the controller Job is deployed, **Then** it scans files, builds the queue, and creates runner K8s Jobs using the K8s API.
2. **Given** a controller that has built N queue batches, **When** it creates runner Jobs, **Then** it creates an appropriate number of runner Jobs with configurable parallelism.
3. **Given** a controller with `--k8s-manifest-out` flag, **When** it finishes queue building, **Then** it writes the generated runner Job manifests to the specified file without applying them to the cluster.
4. **Given** a locally running controller with `--local` flag, **When** it finishes queue building, **Then** it runs an embedded runner inline without creating any K8s resources.

---

### Edge Cases

- What happens when a runner's GCS download fails mid-file? The runner should mark the item as failed (triggering retry logic) and move on to the next item.
- What happens when two runners try to claim the same queue item? The queue mechanism must ensure exactly-once delivery — only one runner succeeds in claiming the item.
- What happens when the expanded GCS folder already contains files from a previous partial expansion? The controller should detect existing expanded files and skip re-expansion (idempotent).
- What happens when a file is larger than the runner's available memory? The runner should detect oversized files (based on configurable thresholds) and either skip them with a logged warning or attempt streaming/temp-file processing.
- What happens when the queue storage becomes unavailable? Runners should fail gracefully with clear error messages rather than silently losing work.
- What happens when a runner processes a file that produces zero chunks (e.g., a binary file that passed classification)? The runner should mark the item as complete with a "skipped — no indexable content" status.

## Requirements *(mandatory)*

### Functional Requirements

#### Provider Abstraction

- **FR-038**: The system MUST define a **source provider** interface (protocol/abstract base class) that abstracts file storage operations: list files (with prefix filtering), download file content, download file to temp path, upload file, check file existence, and compute cache paths. The initial implementation is a GCS source provider. All controller and runner code MUST interact with file storage exclusively through this interface, never directly via GCS-specific APIs.
- **FR-039**: The system MUST define a **destination provider** interface (protocol/abstract base class) that abstracts vector indexing operations: ensure collection exists, index chunks (with embeddings), check existence by source path and content hash, delete by source path, and close connection. The initial implementation is a Qdrant destination provider. All indexing code MUST interact with the vector store exclusively through this interface, never directly via Qdrant-specific APIs.
- **FR-040**: Source and destination provider implementations MUST be selectable via YAML configuration (e.g., `source.provider: gcs`, `destination.provider: qdrant`), with provider-specific settings nested under the provider name. This enables future providers (e.g., S3, local filesystem, Weaviate, Milvus) to be added without modifying core pipeline code.

#### Controller

- **FR-001**: The controller MUST scan the configured source provider (initially GCS) to discover all files to process, using prefix filtering.
- **FR-002**: The controller MUST identify archive files (ZIP, TAR, GZ, BZ2, XZ) and expand them, uploading individual member files to a configurable expansion folder on the source provider.
- **FR-003**: The controller MUST skip re-expansion of archives that have already been expanded in a previous run (idempotent expansion).
- **FR-004**: The controller MUST build a queue of all individual files (both direct source files and expanded archive members) to be processed.
- **FR-005**: The controller MUST exclude files that appear on the skip list (stored on the source provider) unless a force flag is set.
- **FR-006**: The controller MUST classify files during queue building to exclude non-indexable binary files early.
- **FR-007**: The controller MUST support recursive expansion of nested archives up to a configurable depth (default: 2).

#### Runner

- **FR-008**: Each runner MUST process exactly one file at a time — no parallel processing within a runner.
- **FR-009**: The runner MUST claim a file from the queue, download it from the source provider, and process it through the full pipeline: classify → extract → chunk → index.
- **FR-010**: The runner MUST mark each queue item as complete (success or failure) after processing.
- **FR-011**: The runner MUST support processing multiple files in sequence within a single pod lifecycle (not one file per pod).
- **FR-012**: The runner MUST exit gracefully when no more items are available in the queue.
- **FR-013**: The runner MUST monitor its own memory usage and exit gracefully (after completing the current file) when a configurable memory threshold is exceeded (default: 4 GB RSS per pod), allowing the orchestrator to restart it fresh.

#### File Processing

- **FR-014**: The system MUST classify files into file type groups using MIME type detection (via python-magic), file extension matching, and custom content detectors (e.g., MUMPS label patterns and caret density analysis). File type groups are defined in YAML configuration and specify membership criteria (extensions, MIME types, detectors), extraction strategy, and chunking strategy. The built-in defaults file provides standard groups including: `office-documents`, `mumps-source`, `mumps-globals`, `general-source`, `data-files`, `images`, `plain-text`, and `binary`. Image files below a configurable minimum size threshold (default: 50 KB) MUST be skipped from docling OCR processing. Classification resolves to the first matching file type group in declaration order (the order groups appear in the merged YAML configuration).
- **FR-015**: The system MUST extract file content using the extraction strategy specified by the file's type group. Two built-in extractors are supported: `docling` (subprocess-isolated conversion for PDF, Office, HTML, images, audio/video) and `raw-text` (read bytes and decode as text for source code, plain text, data files). Docling conversions MUST run in subprocess isolation (not in-process or via fork-based process pools) to ensure native memory leaks from libpdfium, ONNX runtime, and PyTorch are fully reclaimed by the OS when the child process exits. Each conversion subprocess MUST have a configurable timeout (default: 300 seconds) to kill pathological files. This extraction timeout operates as a safety net within the per-file processing budget (FR-036, default: 600 seconds).
- **FR-016**: The system MUST implement a dual-cache strategy on the source provider: both a `.md` markdown cache (for display/skip detection) and a `.docling.json` serialized DoclingDocument cache (for re-chunking without re-extraction). The runner checks for cached representations before invoking docling extraction.
- **FR-017**: The system MUST chunk documents using the chunker strategy specified by the file's type group. Four built-in strategies are supported: `docling-hybrid` (Docling HybridChunker for docling-extracted documents), `mumps-label-boundary` (custom MUMPS-aware label-boundary chunker for MUMPS routines/globals), `chonkie-code` (Chonkie CodeChunker with tree-sitter AST-based splitting for 165+ programming languages), and `chonkie-recursive` (Chonkie RecursiveChunker for plain text and markdown). Each strategy accepts strategy-specific parameters in the file type group config (e.g., `chunk_size`, `language` hint for code, `recipe` for recursive). Chonkie (`chonkie` package) is a new dependency.
- **FR-018**: The system MUST index chunks to the destination provider with correct metadata (source path, source URL, content hash, chunk index, total chunks, collection name, file size, original format, cache path, indexed timestamp, and chunker-specific metadata such as headings and line ranges). The Qdrant destination provider MUST use named vector format `fast-all-minilm-l6-v2` for compatibility with mcp-server-qdrant.
- **FR-019**: The system MUST skip files that are already indexed in the destination provider with matching content hash (SHA256, truncated to 32 hex characters) unless force mode is enabled. Point IDs MUST be deterministic (derived from source path) to enable idempotent upserts.
- **FR-020**: The system MUST support two configurable file size thresholds: `max_file_size` for documents (default: 50 MB, skip with warning) and `max_source_size` for source code files (default: 10 MB, prevents memory amplification from giant .zwr global exports during tokenization/chunking).

#### Queue and Failure Handling

- **FR-021**: The controller MUST partition the queue into batch files (default: 1000 items per batch), written as individual JSON files to a queue folder on the source provider (e.g., `queue/pending/batch-0001.json`). Batch claiming MUST be atomic — only one runner can successfully claim a given batch. The source provider implementation determines the atomicity mechanism (e.g., GCS uses conditional create with `if_generation_match=0`). The claiming runner moves the batch from `pending/` to `claimed/{runner_id}/`, then deletes the original. Each file within a batch is processed by exactly one runner at a time.
- **FR-022**: Each batch file MUST track the number of processing attempts per item. The runner updates item statuses within its own claimed batch file (no cross-runner contention).
- **FR-023**: The system MUST retry failed items up to a configurable maximum (default: 3 attempts). Items that fail within a batch are written to a `queue/retry/` folder for a subsequent retry pass.
- **FR-024**: After exhausting retries, the system MUST mark items as permanently failed with the last error reason recorded, writing them to a `queue/failed/` folder.
- **FR-025**: The queue MUST support a lease/timeout mechanism — if a runner crashes without completing its claimed batch, other runners MUST detect stale batches in `queue/claimed/` where `claimed_at + lease_timeout < now` (default: 10 minutes) and reclaim them by moving them back to `queue/pending/`.

#### Configuration and Routing

- **FR-026**: The system MUST define file type groups as first-class configuration objects in YAML. Each file type group specifies: membership criteria (lists of file extensions, MIME types, and/or custom content detector names), an extraction strategy (`docling` or `raw-text`), and a chunker strategy with strategy-specific parameters. A built-in defaults file ships with the application containing standard file type groups (e.g., `office-documents`, `mumps-source`, `general-source`, `plain-text`, `images`). User configuration can define new groups and override built-in groups by name — if a user defines a group with the same name as a built-in, the user definition completely replaces it; unmentioned built-in groups are preserved.
- **FR-027**: The system MUST support configurable routing rules that map files to destination collections. Each routing rule specifies match criteria: `file_group` (list of file type group names), `path` (list of path substring or regex patterns), and/or `filename` (list of filename patterns). Criteria types are ANDed together; values within each criterion are ORed. Rules are evaluated in priority order with first-match-wins semantics. By default, source code file type groups are routed to a collection with a `-source` suffix appended to the base collection name (e.g., `vista` → `vista-source`), overridable by explicit routing rules.
- **FR-028**: The system MUST provide a default collection for files that match no routing rules.
- **FR-029**: All pipeline configuration MUST use YAML format (replacing TOML). The configuration MUST cover: source provider selection and settings (e.g., GCS buckets, prefixes, expansion folder), destination provider selection and settings (e.g., Qdrant URL, API key, connection timeout), queue settings (batch size, lease timeout), retry limits, file size thresholds (`max_file_size`, `max_source_size`), file type group definitions, routing rules, memory limits (runner RSS threshold, `MALLOC_ARENA_MAX`), docling conversion timeout, image size threshold, max chunk tokens, embedding model name, max pages per document, archive expansion depth, progress summary interval, and per-file processing timeout. Destination provider connection settings MUST additionally support environment variable overrides (e.g., `QDRANT_URL`, `QDRANT_API_KEY` for Qdrant). Configuration loading order: built-in defaults → user YAML file → environment variable overrides.
- **FR-037**: The built-in defaults file MUST be shipped as a YAML resource within the application package (loaded via `importlib.resources`). Users MUST NOT need to copy or modify this file for standard usage — only override specific groups or add new ones in their project config. The defaults file serves as both the production default and documentation of available file type groups.

#### Operational

- **FR-030**: The system MUST produce JSON-formatted structured logs that include file paths, processing times, success/failure status, and memory usage.
- **FR-031**: The system MUST produce a summary report at the end of each controller and runner execution showing total files, processed, skipped, failed, and permanently failed counts.
- **FR-032**: The system MUST support a dry-run mode where the controller builds the queue and reports what would be processed without actually enqueuing or processing anything.
- **FR-033**: The system MUST reconstruct original source URLs from source provider paths for destination metadata, supporting: httrack mirror comment extraction, WorldVistA GitHub repository URL mapping, and domain-first path reconstruction.
- **FR-034**: The runner MUST apply Linux-specific memory optimizations where available: force glibc to release freed pages (via `malloc_trim`), limit malloc arena count (`MALLOC_ARENA_MAX=2`), and invoke garbage collection between files.
- **FR-035**: The controller MUST skip archive members that are hidden files, macOS resource forks (`__MACOSX`, `._*`), Windows metadata (`Thumbs.db`, `desktop.ini`), and non-extractable archive-like formats within archives (`.jar`, `.war`, `.whl`, `.egg`).
- **FR-036**: The runner MUST enforce a configurable per-file processing timeout (default: 600 seconds). If a file exceeds this timeout, the runner MUST terminate processing for that file, mark it as failed, and continue to the next item.

#### Deployment & Orchestration

- **FR-041**: The system MUST be packaged as a single Docker image with separate entrypoints for controller and runner (e.g., `python -m thresher controller`, `python -m thresher runner`). The Dockerfile MUST produce a reproducible image containing all dependencies (docling, Chonkie with tree-sitter, FastEmbed, python-magic, K8s client).
- **FR-042**: The controller MUST support a `--k8s-deploy` mode where, after building the queue, it programmatically creates runner K8s Jobs using the Kubernetes API (e.g., via the `kubernetes` Python client library). The controller MUST configure each runner Job with: the same Docker image (self-referencing), resource limits (memory, CPU from config), the Thresher YAML config (via ConfigMap or mounted volume), source/destination provider credentials (via K8s Secrets or environment variables), and a unique runner ID.
- **FR-043**: The controller MUST determine the number of runner Jobs to create based on the number of queue batches and a configurable maximum parallelism setting (default: number of batches, capped at a configurable max).
- **FR-044**: The controller MUST support a `--k8s-manifest-out <path>` flag that writes generated runner K8s Job manifests (as YAML) to the specified file instead of applying them to the cluster. This enables GitOps workflows, manual review, or custom deployment pipelines. When this flag is used, the controller MUST NOT interact with the K8s API.
- **FR-045**: The controller MUST support three mutually exclusive execution modes: `--local` (embedded runner, no K8s), `--k8s-deploy` (create runner Jobs via K8s API), and `--k8s-manifest-out` (export manifests only). If none is specified, the controller builds the queue and exits (queue-only mode).
- **FR-046**: K8s Job configuration (namespace, service account, resource requests/limits, node selectors, tolerations, image pull policy, image name/tag) MUST be configurable via the YAML configuration file under a `kubernetes` section. The controller MUST use its own pod metadata (image, namespace, service account) as defaults when running inside a cluster.

### Key Entities

- **Source Provider**: An abstraction over file storage (initially GCS). Interface: list files, download content, download to temp, upload file, check existence, compute cache paths. Provider-specific settings are nested under the provider name in YAML config. Future providers: S3, local filesystem.
- **Destination Provider**: An abstraction over vector indexing (initially Qdrant). Interface: ensure collection, index chunks, check existence by path/hash, delete by source path, close. Provider-specific settings are nested under the provider name in YAML config. Future providers: Weaviate, Milvus, Chroma.
- **Queue Batch**: A JSON file containing up to 1000 queue items (configurable), stored in the source provider's queue folder. Batches move through folders: `queue/pending/` → `queue/claimed/{runner_id}/` → `queue/done/`. Attributes: batch ID, item count, created timestamp, claimed timestamp, runner ID.
- **Queue Item**: A single entry within a queue batch, representing one file to be processed. Attributes: file path (source provider), source type (direct or expanded-from-archive), original archive path (if applicable), status (pending, complete, failed, permanently-failed), attempt count, last error message.
- **Skip List**: A persistent list (stored on the source provider) of file paths that have been successfully processed. Used by the controller to avoid re-queuing completed files.
- **File Type Group**: A configuration object defining a category of files and how to process them. Attributes: name (unique identifier, e.g., `mumps-source`), membership criteria (extensions list, MIME types list, content detector names), extraction strategy (`docling` or `raw-text`), chunker strategy (one of `docling-hybrid`, `chonkie-code`, `chonkie-recursive`, `mumps-label-boundary`) with strategy-specific parameters. Built-in groups are shipped as defaults; user-defined groups with the same name replace them entirely.
- **Routing Rule**: A configuration entry mapping match criteria to a target destination collection. Attributes: match criteria (`file_group` list, `path` list, `filename` list — ANDed across types, ORed within each), target collection name, priority order. By default, source code file type groups are auto-routed to a `-source` suffixed collection unless an explicit routing rule overrides this.
- **Processing Result**: The outcome of a runner processing a single file. Attributes: file path, status (indexed, skipped, failed), collection routed to, chunk count, processing duration, error message (if failed).
- **Expansion Record**: Tracks which archives have been expanded. Attributes: archive GCS path, expansion GCS folder, member count, expansion timestamp. Used for idempotent re-runs.
- **Runner Job Manifest**: A K8s Job spec generated by the controller for runner pod(s). Attributes: image (same as controller), resource limits, config mount, credentials, runner ID, backoff limit. Can be applied directly via K8s API or exported to file.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The pipeline successfully processes the full VistA/RPMS document corpus — at least 99% of files indexed by the current system are indexed by Thresher, measured by comparing source path sets between old and new Qdrant collections.
- **SC-002**: No runner pod exceeds its configured memory limit during normal operation — memory usage remains bounded and predictable across the full corpus.
- **SC-003**: Files that cause OOM or crashes are automatically retried and, after 3 failures, permanently marked as failed without blocking the rest of the pipeline.
- **SC-004**: A full pipeline run completes within 120% of the current system's wall-clock time when using the same number of parallel workers (runner pods). Measured by comparing total elapsed time for the same corpus at equal parallelism.
- **SC-005**: Re-running the pipeline after a partial failure resumes from where it left off, processing only unfinished files, with less than 1% duplicate processing.
- **SC-006**: An operator can configure the pipeline for a new document archive (different bucket, different routing rules) using only configuration changes — no code modifications required.
- **SC-007**: Each runner pod processes at least 100 small files (< 1MB) per pod lifecycle, demonstrating efficient sequential processing without excessive startup overhead.
- **SC-008**: 100% of permanently failed files have a recorded error reason that is actionable for debugging.

## Assumptions

- The source and destination providers are abstracted behind interfaces, with GCS and Qdrant as the initial implementations. The interface contracts are designed around current GCS/Qdrant capabilities; future providers must implement the same interface but may have different concurrency or atomicity characteristics (e.g., S3 conditional writes differ from GCS `if_generation_match`).
- The GCS bucket structure and content remain similar to the current VistA/RPMS archive layout.
- Kubernetes is the target orchestration platform. The controller uses the `kubernetes` Python client library to create runner Jobs. For local development and testing, the controller and runner are separate CLI commands in the same Python package. An operator can run the controller to build the queue, then run one or more runner processes. A convenience `--local` flag on the controller optionally runs an embedded runner inline after queue building, enabling single-command local execution.
- CI builds and pushes the Docker image; CI then deploys the controller K8s Job. The controller is responsible for creating runner Jobs — CI does not need to know about runner parallelism or queue shape. Image building/pushing is environment-dependent and out of scope for the application code (a sample Dockerfile is provided, not a CI pipeline definition).
- The existing docling extraction, MUMPS chunking, and Qdrant indexing logic is functionally correct and will be preserved — the rebuild focuses on the orchestration, configuration, and memory management architecture. The Chonkie library (`chonkie` package with `[code]` extra for tree-sitter support) will be adopted for non-MUMPS source code chunking (CodeChunker) and plain text/markdown chunking (RecursiveChunker), replacing the current text-fallback chunker.
- The queue mechanism will use pre-partitioned GCS batch files (default: 1000 items per batch). The controller writes batches to `queue/pending/`; runners claim entire batches atomically via GCS conditional create. This scales to 500K+ items with zero contention between runners (each batch file is ~200 KB). If higher throughput is needed later, a message broker (e.g., Pub/Sub) can replace it without changing the runner interface.
- Old source code will be moved to a gitignored directory before new code is written, providing a clean start while preserving reference material.
- Archive expansion to GCS is a one-time operation per archive — expanded files persist across runs and do not need to be re-expanded unless the source archive changes.
- The existing embedding model (sentence-transformers/all-MiniLM-L6-v2) and Qdrant vector configuration (named vectors compatible with mcp-server-qdrant) will be preserved.

## Clarifications

### Session 2025-03-25

- Q: What concrete mechanism should the queue use for atomic claim/release (FR-021, FR-025)? → A: Pre-partitioned GCS batch files (1000 items/batch). Controller writes batches to `queue/pending/`; runners claim batches atomically via GCS conditional create. Scales to 500K+ items with zero contention.
- Q: What should the default lease timeout be for queue items (FR-025)? → A: 10 minutes, matching the docling conversion timeout.
- Q: What should the default runner memory threshold be (FR-013)? → A: 4 GB RSS per pod.
- Q: How should source-code routing to `-source` collections work in the generalized config (FR-026)? → A: Keep `-source` suffix as default convention, overridable by explicit category-based routing rules.
- Q: How should local development mode work (controller/runner split)? → A: Separate CLI commands in same package; `--local` flag on controller runs embedded runner inline.

### Session 2025-03-25 (Configurability)

- Q: Should classification data (file extension lists, MIME types, detection patterns) be configurable? → A: Yes — switch to YAML config with file type groups as first-class concept. Built-in defaults file ships standard groups; users can extend or override. File type groups define membership via extensions, MIME types, and custom content detectors.
- Q: How should user config interact with built-in defaults? → A: Override per-group — if a user defines a group with the same name as a built-in, the user definition completely replaces it. Unmentioned built-in groups are preserved.
- Q: How should routing rules combine match criteria? → A: AND across criteria types (file_group, path, filename), OR within each criterion via lists. E.g., `file_group: [mumps-source, java-source]` AND `path: [rpms/, ihs/]` matches (mumps-source OR java-source) AND (rpms/ OR ihs/). Multiple rules provide additional OR via first-match-wins.
- Q: Should file type groups specify chunker strategy? → A: Yes — each group specifies its chunker strategy and strategy-specific params. Four strategies: `docling-hybrid`, `chonkie-code` (tree-sitter AST, 165+ languages), `chonkie-recursive` (plain text/markdown), `mumps-label-boundary` (custom). Chonkie is a new dependency.
- Q: Should file type groups specify extraction strategy? → A: Yes — each group is a complete processing recipe (membership + extractor + chunker). Two extractors: `docling` (subprocess-isolated) and `raw-text`. Eliminates hardcoded DOCLING_MIME_TYPES dispatch.
- Q: Should source (GCS) and destination (Qdrant) be abstracted behind interfaces? → A: Yes — both source and destination are defined as provider interfaces (protocols/ABCs). GCS and Qdrant are the initial implementations. Provider selection and settings are in YAML config. Enables future S3, local filesystem, Weaviate, Milvus, etc. without modifying core pipeline code.

### Session 2025-03-25 (Deployment)

- Q: Single Docker image or separate? → A: Single image with different entrypoints for controller and runner.
- Q: Who creates runner K8s Jobs? → A: Controller-as-orchestrator — after building the queue, the controller uses the K8s API to create runner Jobs. Option to export manifests to file (`--k8s-manifest-out`) for custom deployment.
- Q: What does the CI pipeline do? → A: CI builds/pushes the Docker image and deploys the controller Job. The controller then self-orchestrates runner Jobs based on queue analysis. CI does not need to know about runner parallelism.
- Q: Controller execution modes? → A: Three mutually exclusive modes: `--local` (embedded runner), `--k8s-deploy` (create Jobs via K8s API), `--k8s-manifest-out` (export only). Default: queue-only.
