# Feature Specification: Parallel Archive Expansion & Batched Uploads

**Feature Branch**: `006-parallel-archive-expansion`
**Created**: 2026-03-27
**Status**: Draft
**Input**: User description: "Scale out zip decompression across K8s jobs and batch GCS uploads for faster archive processing"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Parallel Archive Expansion via Jobs (Priority: P1)

As an operator processing a dataset with hundreds of large archives, I need archive expansion to happen in parallel across multiple jobs so that the expansion phase completes in minutes rather than hours.

**Why this priority**: Archive expansion is currently the primary bottleneck — it runs serially in the controller, downloading, extracting, and uploading one archive at a time. A dataset with 200 archives of 100MB each can take hours. Parallelizing this across K8s jobs provides the largest throughput improvement.

**Independent Test**: Upload 10 ZIP archives to the source bucket. Run the controller. Verify that multiple expansion jobs are created and run concurrently, and that all archive members appear under the expanded prefix before processing begins.

**Acceptance Scenarios**:

1. **Given** a source bucket contains 10 ZIP archives and the system is configured with `max_expansion_parallelism: 5`, **When** the controller runs, **Then** up to 5 expansion jobs run concurrently and all archives are fully expanded before processing batches are built.
2. **Given** the controller is run with `--local` mode, **When** archives are present, **Then** expansion still works correctly in single-process mode (sequential fallback) without requiring K8s.
3. **Given** an expansion job fails mid-way (e.g., corrupted archive), **When** the controller detects the failure, **Then** it marks that archive as failed, continues with remaining archives, and reports the failure in the summary.
4. **Given** archives were already expanded in a previous run (expansion records exist), **When** the controller runs again, **Then** already-expanded archives are skipped (idempotency preserved).

---

### User Story 2 — Batched GCS Uploads During Expansion (Priority: P2)

As an operator, I need expanded archive members to be uploaded to GCS in batches rather than one at a time, so that expansion jobs complete faster and make better use of network bandwidth.

**Why this priority**: Each archive may contain hundreds or thousands of files. Uploading them one at a time adds significant per-request overhead. Batching uploads reduces this overhead and improves throughput.

**Independent Test**: Expand a ZIP archive with 500 small files. Verify that files are uploaded in concurrent groups rather than individual sequential calls, and that all files are present in GCS after completion.

**Acceptance Scenarios**:

1. **Given** an archive containing 500 files and `upload_batch_size: 50`, **When** expansion runs, **Then** files are uploaded in concurrent groups of up to 50, reducing total upload time compared to sequential uploads.
2. **Given** an upload batch partially fails (e.g., 3 of 50 files fail due to transient error), **When** the batch completes, **Then** failed uploads are retried before the expansion is marked complete.
3. **Given** a single very large file within an archive, **When** it is uploaded, **Then** it is uploaded individually without being held back by batching logic.

---

### User Story 3 — Expansion Queue Coordination (Priority: P3)

As an operator, I need the controller to coordinate expansion and processing phases so that processing only begins after all archives are expanded, ensuring expanded files are included in processing batches.

**Why this priority**: If processing batches are built before expansion completes, expanded files will be missed. The controller must ensure a clear phase boundary: expand first, then scan expanded results and build processing batches.

**Independent Test**: Run the controller with archives and direct files. Verify that processing queue batches include both direct files and all expanded archive members.

**Acceptance Scenarios**:

1. **Given** a mix of direct files and archives in the source bucket, **When** the controller runs with `--k8s-deploy`, **Then** expansion jobs complete before processing queue batches are built, and batches include all expanded members.
2. **Given** the controller is run with `--dry-run`, **When** archives are present, **Then** the dry-run report shows estimated archive member counts without actually expanding (based on previous expansion records or archive metadata if available).
3. **Given** expansion jobs are deployed via K8s, **When** the controller waits for completion, **Then** it polls job status with a configurable timeout and reports progress.

---

### Edge Cases

- What happens when a ZIP archive contains another ZIP archive (nested archives)? Nested expansion must still respect `archive_depth` limits, applied per expansion job.
- What happens when two archives contain files with identical paths? The expanded prefix includes the archive stem, so collisions are avoided (`expanded/archive1/file.txt` vs `expanded/archive2/file.txt`).
- What happens when an archive is encrypted or password-protected? The expansion job should report the error, mark the archive as failed, and continue with other archives.
- What happens when the controller is interrupted during the expansion phase? On re-run, expansion records allow completed archives to be skipped; in-progress expansions (no record yet) are re-attempted.
- What happens when GCS has a rate limit or throttling? Batched uploads should use exponential backoff consistent with existing retry configuration.
- What happens with very large archives (e.g., 10GB with 100K files)? A single expansion job handles one archive; the job's memory and storage constraints must be sufficient for the largest expected archive.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The controller MUST support distributing archive expansion across multiple concurrent expansion jobs (K8s or local).
- **FR-002**: The controller MUST maintain a two-phase workflow: (1) expand all archives, (2) scan expanded results and build processing queue batches.
- **FR-003**: Expansion jobs MUST upload extracted files to GCS using concurrent batch uploads rather than sequential single-file uploads.
- **FR-004**: The number of concurrent expansion jobs MUST be configurable via `processing.max_expansion_parallelism` (default: 5).
- **FR-005**: The upload batch concurrency MUST be configurable via `processing.upload_batch_size` (default: 50).
- **FR-006**: Expansion jobs MUST preserve existing idempotency behavior — if an expansion record already exists for an archive, the archive MUST be skipped.
- **FR-007**: Failed expansion jobs MUST NOT block other expansion jobs from completing.
- **FR-008**: The controller MUST report expansion results: archives expanded, files extracted, archives failed, and total expansion time.
- **FR-009**: In `--local` mode, the controller MUST fall back to running expansion with local concurrency without requiring K8s.
- **FR-010**: Expansion jobs MUST respect existing `processing.archive_depth`, `processing.archive_exclude_extensions`, and archive filtering configuration.
- **FR-011**: The controller MUST wait for all expansion jobs to complete (or fail/timeout) before proceeding to queue building.
- **FR-012**: Expansion jobs MUST write expansion records on completion, consistent with current `ExpansionRecord` format.
- **FR-013**: Batched uploads MUST retry transient failures with exponential backoff, consistent with existing retry configuration.
- **FR-014**: The system MUST support a new CLI subcommand or mode (e.g., `thresher expander`) that expansion jobs invoke, analogous to `thresher runner`.

### Key Entities

- **Expansion Batch**: A group of archive paths assigned to a single expansion job for parallel processing. Contains archive paths, configuration, and status tracking.
- **Expansion Job**: A K8s Job (or local process) that downloads, extracts, and uploads members for one or more assigned archives. Reports results via expansion records on GCS.
- **Expansion Record** (existing): Per-archive JSON recording archive path, member count, hash, and completion time. Used for idempotency.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Expansion of 100 archives completes at least 5x faster with parallel jobs (5 concurrent) compared to sequential expansion.
- **SC-002**: Upload throughput for expanded archive members improves at least 3x with batched uploads compared to sequential single-file uploads.
- **SC-003**: All expanded files are included in processing queue batches — zero files lost between expansion and processing phases.
- **SC-004**: Existing archive expansion behavior (idempotency, depth limits, exclude filters) continues to work identically in both local and K8s modes.
- **SC-005**: Operator can observe expansion progress (jobs created, archives completed, archives failed) from controller logs.

## Assumptions

- Archives are stored as objects in the source provider (GCS) and each expansion job can independently download and process its assigned archives.
- The storage provider client supports concurrent uploads from a single process without requiring application-level thread management.
- Expansion jobs are lightweight enough that the same container image used for runner jobs can also serve as the expansion job image.
- In local mode, concurrency is achieved within the controller process without spawning subprocesses for expansion.
- The number of archives in a typical dataset is in the range of tens to thousands; the expansion batch assignment algorithm does not need to optimize for millions of archives.
- Expansion records are small JSON files and can be reliably written atomically; they serve as the completion signal for the controller to detect finished expansion jobs.
