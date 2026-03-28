# Implementation Plan: Parallel Archive Expansion & Batched Uploads

**Branch**: `006-parallel-archive-expansion` | **Date**: 2026-03-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/006-parallel-archive-expansion/spec.md`

## Summary

Archive expansion currently runs serially in the controller — downloading, extracting, and uploading one archive at a time. For datasets with hundreds of archives, this is the primary throughput bottleneck. This feature parallelizes expansion across K8s Jobs (one archive per job) and adds concurrent batch uploads within each job, while maintaining the existing two-phase workflow: expand all archives first, then build processing queue batches.

## Technical Context

**Language/Version**: Python 3.11+ (Docker image: 3.13)
**Primary Dependencies**: google-cloud-storage, kubernetes, pyyaml, jsonschema
**Storage**: GCS (object storage via SourceProvider protocol)
**Testing**: pytest (unit + functional with Docker services)
**Target Platform**: Linux containers on Kubernetes
**Project Type**: CLI / cloud-native pipeline
**Performance Goals**: 5x faster expansion (parallel jobs), 3x faster uploads (batch concurrency)
**Constraints**: Memory-bounded (4GB default per job), stateless jobs, GCS rate limits
**Scale/Scope**: Tens to thousands of archives, each containing tens to hundreds of thousands of files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Configuration-Driven Design | ✅ Pass | New config fields: `max_expansion_parallelism`, `upload_batch_size` with sensible defaults |
| II. Extensible Architecture | ✅ Pass | Expansion uses existing SourceProvider protocol; no new interfaces coupled to GCS |
| III. Reliability First | ✅ Pass | Per-archive idempotency via expansion records; failed jobs don't block others; retry with backoff on uploads |
| IV. Performance at Scale | ✅ Pass | Core goal: parallel jobs + concurrent uploads; configurable concurrency limits |
| V. Cloud-Native Design | ✅ Pass | Stateless expansion jobs on K8s; state in GCS expansion records; same container image |

**Gate result: PASS** — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/006-parallel-archive-expansion/
 plan.md              # This file
 spec.md              # Feature specification
 research.md          # Phase 0: research findings
 data-model.md        # Phase 1: data model updates
 quickstart.md        # Phase 1: getting started
 contracts/           # Phase 1: interface contracts
   └── expansion-job.md # Expansion job CLI contract
 checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
thresher/
 cli.py                          # Add `expander` subcommand
 config.py                       # Add max_expansion_parallelism, upload_batch_size
 config_schema.json              # Update schema with new fields
 defaults.yaml                   # Add default values
 controller/
   ├── archive_expander.py         # Add concurrent upload support
   ├── expansion_orchestrator.py   # NEW: expansion job distribution + wait logic
   ├── k8s_orchestrator.py         # Add build_expansion_job_specs()
   └── scanner.py                  # Decouple archive detection from expansion
 types.py                        # Add ExpansionBatchSpec if needed

tests/
 unit/
   ├── test_archive_expander.py    # Update for concurrent uploads
   ├── test_expansion_orchestrator.py  # NEW: orchestration tests
   └── test_k8s_orchestrator.py    # Add expansion job spec tests
 functional/
    └── test_expansion_e2e.py       # NEW: parallel expansion e2e tests
```

**Structure Decision**: New code follows the existing controller/ module pattern. The expansion orchestrator is a new module that coordinates expansion job deployment and wait logic, keeping k8s_orchestrator focused on job spec generation and deployment.

## Design Decisions

### 1. One Archive Per Job (from clarification)
Each expansion job handles exactly one archive. This maximizes failure isolation (a corrupted archive fails only its job), simplifies resource sizing, and maps naturally to K8s Job semantics. The controller creates up to `max_expansion_parallelism` concurrent jobs.

### 2. Controller Waits for Expansion Completion
The controller deploys expansion jobs, then polls for completion by checking expansion records on GCS. Once all archives have expansion records (or jobs have failed), the controller proceeds to scan expanded files and build processing queue batches.

### 3. Concurrent Uploads via ThreadPoolExecutor
Within each expansion job, extracted archive members are uploaded using `concurrent.futures.ThreadPoolExecutor` with `upload_batch_size` workers. This is safe because GCS uploads are I/O-bound and the google-cloud-storage client is thread-safe. No asyncio needed.

### 4. Local Mode Uses ThreadPoolExecutor Too
In `--local` mode, the controller runs expansion in-process using a ThreadPoolExecutor for archive-level parallelism (up to `max_expansion_parallelism` workers) with nested thread pools for uploads. No K8s required.

### 5. New `expander` CLI Subcommand
Expansion jobs invoke `thresher expander --archive-path <path>`. This is analogous to `thresher runner --runner-id <id>`. The expander downloads, extracts, uploads members concurrently, writes the expansion record, and exits.

### 6. Completion Detection via Expansion Records
The controller detects expansion completion by checking for `.expansion-record.json` files — the same mechanism already used for idempotency. No new coordination protocol needed. Failed jobs are detected via K8s Job status (Failed state).

## Complexity Tracking

No constitution violations to justify.
