# Research: Parallel Archive Expansion & Batched Uploads

## R1: GCS Concurrent Upload Patterns

**Decision**: Use `concurrent.futures.ThreadPoolExecutor` to upload archive members in parallel within each expansion job.

**Rationale**: The `google-cloud-storage` Python client is thread-safe for upload operations. Each `blob.upload_from_filename()` call is independent and I/O-bound, making it ideal for thread-based concurrency. No need for asyncio or multiprocessing.

**Alternatives considered**:
- **GCS `Bucket.batch()` context manager**: Only supports metadata operations (delete, patch), not uploads. Not applicable.
- **`gcs-parallel-upload` or `gsutil -m`**: External tooling, not embeddable in Python.
- **asyncio with `aiohttp`**: Would require rewriting the GCS provider to async, which is out of scope and unnecessary for I/O-bound uploads.
- **multiprocessing.Pool**: Overkill for I/O-bound work; thread pool is simpler and avoids serialization overhead.

## R2: K8s Job Polling for Completion

**Decision**: Controller polls K8s Job status via the Batch API at a configurable interval (default 10s) with a configurable timeout (reuse `processing.per_file_timeout` scaled by archive count, or a new `expansion_timeout` field).

**Rationale**: K8s Jobs have well-defined completion semantics (`status.succeeded`, `status.failed`). Polling is simple and reliable. The controller already has `kubernetes` as a dependency.

**Alternatives considered**:
- **K8s Watch API**: More efficient but adds complexity (connection management, reconnection). Polling is sufficient for tens-to-hundreds of jobs.
- **GCS-based completion signal only**: Already planned (expansion records), but needs K8s Job status to detect failures (jobs that crash before writing a record).
- **Webhook/callback**: Requires a controller HTTP endpoint, adding operational complexity.

## R3: Local Mode Parallelism

**Decision**: In `--local` mode, use a `ThreadPoolExecutor` with `max_expansion_parallelism` workers. Each worker calls the existing `ArchiveExpander._expand_single()` method for one archive.

**Rationale**: Reuses existing expansion code path. Thread pool provides parallelism without K8s. Memory is bounded by `max_expansion_parallelism` concurrent archive extractions.

**Alternatives considered**:
- **Sequential (current behavior)**: Too slow for the feature's goals.
- **multiprocessing.Pool**: Would work but adds complexity (pickling, IPC). Expansion is I/O-bound, not CPU-bound.
- **asyncio.gather**: Would require rewriting expand_single to async.

## R4: Archive-to-Job Assignment

**Decision**: One archive per job (per spec clarification). Controller creates one K8s Job per archive, throttled by `max_expansion_parallelism`. Jobs beyond the limit wait in the K8s queue (using Job `parallelism: 1` and K8s scheduling).

**Rationale**: Simplest model. Best failure isolation. Natural fit for K8s Jobs. Resource sizing is per-archive (each job needs enough disk/memory for its one archive).

**Alternatives considered**:
- **Multiple archives per job**: Reduces job overhead but complicates failure handling (one bad archive can waste work on good ones in the same batch).
- **Adaptive batching**: Complex to implement; overhead savings are marginal for typical archive counts.

## R5: Scanner Refactoring

**Decision**: Split scanner into two phases: (1) detect and collect archives, (2) scan expanded results after expansion completes. The `scan_files()` function currently does both in one pass; it needs to return archives separately so the controller can dispatch expansion before scanning expanded results.

**Rationale**: The current `scan_files()` calls `ArchiveExpander.expand_archives()` inline. For parallel expansion, the controller must run expansion as a separate step (via K8s jobs or local thread pool) before rescanning the expanded prefix.

**Alternatives considered**:
- **Keep scanner monolithic, add parallelism inside**: Would require the scanner to manage K8s jobs, violating separation of concerns.
- **New scanner class**: Unnecessary; splitting the existing function into two calls is sufficient.
