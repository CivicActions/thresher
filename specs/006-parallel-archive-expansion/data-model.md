# Data Model: Parallel Archive Expansion

## Modified Entities

### ProcessingConfig (existing, updated)

New fields added to `thresher/config.py`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_expansion_parallelism` | int | 5 | Maximum concurrent expansion jobs (K8s) or threads (local) |
| `upload_batch_size` | int | 50 | Maximum concurrent file uploads within each expansion job |
| `expansion_timeout` | int | 3600 | Maximum seconds to wait for all expansion jobs to complete |

### ExpansionRecord (existing, unchanged)

| Field | Type | Description |
|-------|------|-------------|
| `archive_path` | str | Source path of the archive |
| `expansion_folder` | str | GCS prefix where members were expanded |
| `member_count` | int | Number of files extracted |
| `expanded_at` | float | Unix timestamp of completion |
| `archive_hash` | str or None | MD5 hash for idempotency |

No changes needed. Expansion records continue to serve as the per-archive completion signal.

## New Entities

### ExpansionJobSpec

Represents the K8s Job specification for an expansion job. Not a persistent data type — built in memory by the orchestrator and submitted to K8s.

| Field | Type | Description |
|-------|------|-------------|
| `archive_path` | str | Source path of the archive to expand |
| `job_name` | str | K8s Job name: `thresher-expander-{archive_stem}` |

### ExpansionResult

Returned by the expansion orchestrator to the controller after all expansion jobs complete.

| Field | Type | Description |
|-------|------|-------------|
| `archives_expanded` | int | Successfully expanded archives |
| `archives_failed` | int | Failed expansions |
| `files_extracted` | int | Total files uploaded across all archives |
| `duration_seconds` | float | Total wall-clock time for expansion phase |
| `failed_archives` | list[str] | Paths of archives that failed |

## State Transitions

### Expansion Job Lifecycle

```
Archive detected by scanner
        │
        ▼
  Job Created (K8s pending)
        │
        ▼
  Job Running
  ├─ Download archive
  ├─ Extract to temp dir
  ├─ Upload members (concurrent batch)
  └─ Write expansion record
        │
    ┌───┴───┐
    ▼       ▼
 Success  Failed
 (record  (no record;
  exists)  K8s job
           status=Failed)
```

### Controller Expansion Phase

```
scan_files_direct() → direct files + archive list
        │
        ▼
  Deploy expansion jobs (or local thread pool)
        │
        ▼
  Poll for completion (expansion records + K8s status)
        │
        ▼
  scan_expanded_files() → expanded file items
        │
        ▼
  build_queue(direct + expanded items)
```
