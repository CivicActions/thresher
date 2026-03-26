# Data Model: Thresher

**Spec**: [spec.md](spec.md) | **Date**: 2025-03-25

## Entity Relationship Overview

```
┌─────────────────┐      uses       ┌──────────────────┐
│ Source Provider  │◄───────────────│    Controller     │
│  (GCS impl)     │                 │                   │
└────────┬────────┘                 └───────┬───────────┘
         │                                  │
         │  stores/reads                    │ creates
         ▼                                  ▼
┌─────────────────┐              ┌──────────────────┐
│  Queue Batch    │◄─── claims ──│     Runner       │
│  (pending/      │              │                   │
│   claimed/done) │              └───────┬───────────┘
└────────┬────────┘                      │
         │ contains                      │ processes
         ▼                               ▼
┌─────────────────┐              ┌──────────────────┐
│   Queue Item    │              │ Processing Result │
└─────────────────┘              └───────┬───────────┘
                                         │ indexes to
                                         ▼
                                ┌──────────────────┐
                                │ Dest. Provider   │
                                │  (Qdrant impl)   │
                                └──────────────────┘

┌─────────────────┐  classifies   ┌──────────────────┐  routes to   ┌──────────────────┐
│ File Type Group │──────────────►│   Routing Rule   │─────────────►│   Collection     │
└─────────────────┘               └──────────────────┘              └──────────────────┘
```

---

## Entities

### 1. FileTypeGroup

A configuration object defining a category of files and their complete processing recipe.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | yes | Unique identifier (e.g., `mumps-source`, `office-documents`) |
| `extensions` | list[str] | no | File extensions (e.g., `[".m", ".ro", ".zwr"]`) |
| `mime_types` | list[str] | no | MIME type prefixes (e.g., `["text/x-mumps"]`) |
| `detectors` | list[str] | no | Custom content detector names (e.g., `["mumps-labels", "caret-density"]`) |
| `extractor` | str | yes | Extraction strategy: `"docling"` or `"raw-text"` |
| `chunker` | ChunkerConfig | yes | Chunking strategy and parameters |
| `priority` | int | no | Classification priority (lower = checked first, default: 100) |

**Validation rules**:
- At least one of `extensions`, `mime_types`, or `detectors` must be specified
- `extractor` must be one of the registered extractor names
- `chunker.strategy` must be one of the registered chunker strategy names
- `name` must be unique across built-in and user-defined groups

**State transitions**: None (static configuration)

**YAML representation**:
```yaml
file_type_groups:
  mumps-source:
    extensions: [".m", ".ro"]
    detectors: ["mumps-labels"]
    extractor: raw-text
    chunker:
      strategy: mumps-label-boundary
      chunk_size: 512
  
  general-source:
    extensions: [".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs"]
    extractor: raw-text
    chunker:
      strategy: chonkie-code
      chunk_size: 512
      language: auto  # strategy-specific param
  
  office-documents:
    extensions: [".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm"]
    mime_types: ["application/pdf", "application/vnd.openxmlformats"]
    extractor: docling
    chunker:
      strategy: docling-hybrid
      chunk_size: 512
```

### 2. ChunkerConfig

Nested configuration for a chunking strategy within a file type group.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `strategy` | str | yes | One of: `docling-hybrid`, `chonkie-code`, `chonkie-recursive`, `mumps-label-boundary` |
| `chunk_size` | int | no | Max tokens per chunk (default: 512) |
| `language` | str | no | Language hint for `chonkie-code` (default: `"auto"`) |
| `recipe` | str | no | Splitting recipe for `chonkie-recursive` (e.g., `"markdown"`) |

**Strategy-specific parameters** are passed through to the underlying chunker. Unknown parameters for a given strategy are ignored with a warning.

### 3. RoutingRule

A configuration entry mapping match criteria to a target destination collection.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | no | Human-readable rule name (for logging) |
| `file_group` | list[str] | no | File type group names to match (ORed) |
| `path` | list[str] | no | Path substring or regex patterns (ORed) |
| `filename` | list[str] | no | Filename glob patterns (ORed) |
| `collection` | str | yes | Target destination collection name |

**Matching semantics**:
- Criteria types are **ANDed**: all specified criteria must match
- Values within each criterion are **ORed**: any value in the list can match
- Rules are evaluated in **declaration order** with **first-match-wins**
- At least one criterion must be specified
- Default source-code routing (file type groups → `-source` suffix) applies when no explicit rule matches a source-code group

**YAML representation**:
```yaml
routing:
  rules:
    - name: rpms-source-code
      file_group: ["mumps-source", "general-source"]
      path: ["rpms/"]
      collection: rpms-source

    - name: vista-docs
      file_group: ["office-documents", "plain-text"]
      path: ["vista/"]
      collection: vista

    - name: all-source-default
      file_group: ["mumps-source", "general-source"]
      collection: vista-source

  default_collection: vista
  source_suffix: "-source"  # auto-applied to source-code groups if no rule matches
```

### 4. QueueBatch

A JSON file containing a batch of queue items, stored on the source provider.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `batch_id` | str | yes | Unique batch identifier (e.g., `batch-0001`) |
| `created_at` | float | yes | Unix timestamp of batch creation |
| `claimed_at` | float | no | Unix timestamp when claimed by a runner |
| `runner_id` | str | no | ID of the runner that claimed this batch |
| `item_count` | int | yes | Number of items in this batch |
| `items` | list[QueueItem] | yes | The queue items |

**Storage path lifecycle**:
```
queue/pending/batch-0001.json     → created by controller
queue/claimed/{runner_id}/batch-0001.json  → claimed by runner (atomic)
queue/done/batch-0001.json        → completed by runner
queue/retry/batch-0001.json       → contains failed items for retry pass
queue/failed/batch-0001.json      → permanently failed items
```

**State transitions**:
```
pending ──claim──► claimed ──complete──► done
                      │
                      ├──partial-fail──► retry (failed items) + done (succeeded items)
                      │
                      └──crash/timeout──► [stale detection] ──► pending (re-enqueued)
```

**Size constraint**: Default 1000 items per batch (~200 KB JSON). Configurable via `queue.batch_size`.

### 5. QueueItem

A single entry within a queue batch, representing one file to process.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Source provider path to the file |
| `source_type` | str | yes | `"direct"` or `"expanded"` |
| `archive_path` | str | no | Original archive path (if `source_type == "expanded"`) |
| `file_type_group` | str | no | Pre-classified file type group name (from controller scan) |
| `file_size` | int | no | File size in bytes (from listing) |
| `status` | str | yes | Current status (see below) |
| `attempt_count` | int | yes | Number of processing attempts (default: 0) |
| `last_error` | str | no | Error message from last failed attempt |
| `completed_at` | float | no | Unix timestamp when processing completed |

**Status values**: `pending`, `processing`, `complete`, `failed`, `permanently-failed`

**Validation rules**:
- `path` must be non-empty
- `source_type` must be `"direct"` or `"expanded"`
- If `source_type == "expanded"`, `archive_path` must be set
- `attempt_count` must be ≥ 0

### 6. SourceProvider (Protocol)

Abstract interface for file storage operations.

| Method | Signature | Description |
|--------|-----------|-------------|
| `list_files` | `(prefix: str, recursive: bool) → Iterator[FileInfo]` | List files with optional prefix filtering |
| `download_content` | `(path: str) → bytes` | Download file content as bytes |
| `download_to_path` | `(path: str, local_path: Path) → Path` | Download to local filesystem |
| `upload_content` | `(path: str, data: bytes, if_generation_match: int \| None) → None` | Upload content, optional conditional write |
| `upload_from_path` | `(path: str, local_path: Path) → None` | Upload from local filesystem |
| `exists` | `(path: str) → bool` | Check if file exists |
| `delete` | `(path: str) → None` | Delete a file (idempotent) |
| `cache_path` | `(source_path: str, suffix: str) → str` | Compute cache path for a source file |

**FileInfo** (returned by `list_files`):

| Field | Type | Description |
|-------|------|-------------|
| `path` | str | Full path on the provider |
| `size` | int | File size in bytes |
| `updated` | datetime | Last modified timestamp |
| `content_type` | str | MIME type if available |

**Initial implementation**: `GCSSourceProvider` wrapping `google.cloud.storage.Client`

### 7. DestinationProvider (Protocol)

Abstract interface for vector indexing operations.

| Method | Signature | Description |
|--------|-----------|-------------|
| `ensure_collection` | `(name: str, vector_size: int, vector_name: str) → None` | Create collection if not exists |
| `index_chunks` | `(collection: str, chunks: list[IndexChunk]) → None` | Batch upsert chunks with embeddings |
| `exists_by_hash` | `(collection: str, source_path: str, content_hash: str) → bool` | Check if already indexed with matching hash |
| `delete_by_source` | `(collection: str, source_path: str) → None` | Delete all points for a source file |
| `close` | `() → None` | Close connection / cleanup resources |

**IndexChunk** (input to `index_chunks`):

| Field | Type | Description |
|-------|------|-------------|
| `point_id` | str | Deterministic UUID derived from source path + chunk index |
| `vector` | list[float] | Embedding vector (384 dims for all-MiniLM-L6-v2) |
| `payload` | dict | Metadata payload (see Qdrant payload schema) |

**Initial implementation**: `QdrantDestinationProvider` wrapping `qdrant_client.QdrantClient`

### 8. ProcessingResult

Outcome of a runner processing a single file.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Source provider path |
| `status` | str | yes | `"indexed"`, `"skipped"`, `"failed"` |
| `collection` | str | no | Destination collection (if indexed) |
| `chunk_count` | int | no | Number of chunks indexed |
| `duration_seconds` | float | yes | Processing time in seconds |
| `error_message` | str | no | Error details (if failed) |
| `content_hash` | str | no | SHA256 hash (truncated to 32 hex chars) |
| `file_type_group` | str | no | Classified file type group |

### 9. ExpansionRecord

Tracks which archives have been expanded (for idempotent re-runs).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `archive_path` | str | yes | Source provider path to the archive |
| `expansion_folder` | str | yes | Source provider path to expanded files folder |
| `member_count` | int | yes | Number of files extracted |
| `expanded_at` | float | yes | Unix timestamp of expansion |
| `archive_hash` | str | no | Hash of archive file (for change detection) |

**Storage**: JSON file on source provider at `expansion-records/{archive_name}.json`

### 10. RunnerJobManifest

K8s Job spec generated by the controller for runner pods.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `job_name` | str | yes | K8s Job name (e.g., `runner-batch-0001`) |
| `namespace` | str | yes | K8s namespace |
| `image` | str | yes | Docker image (same as controller) |
| `runner_id` | str | yes | Unique runner ID (matches queue claiming) |
| `batch_ids` | list[str] | no | Batch IDs assigned to this runner (for reference) |
| `resource_requests` | dict | yes | CPU/memory requests |
| `resource_limits` | dict | yes | CPU/memory limits |
| `service_account` | str | no | K8s service account name |
| `config_mount` | str | yes | Path to mounted config (ConfigMap or volume) |
| `env_vars` | dict | no | Additional environment variables |
| `labels` | dict | yes | K8s labels (app, job-type, batch-id, etc.) |
| `backoff_limit` | int | yes | K8s retry count (default: 3) |
| `ttl_seconds` | int | no | Time to live after Job finishes (default: 3600) |

**State transitions**: Generated → Applied (via K8s API) or Exported (via `--k8s-manifest-out`)

### 11. ThresherConfig

Top-level configuration object loaded from YAML.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | SourceConfig | yes | Source provider selection and settings |
| `destination` | DestConfig | yes | Destination provider selection and settings |
| `file_type_groups` | dict[str, FileTypeGroup] | yes | File type group definitions |
| `routing` | RoutingConfig | yes | Routing rules and default collection |
| `queue` | QueueConfig | yes | Queue settings (batch size, lease timeout) |
| `processing` | ProcessingConfig | yes | File size limits, timeouts, memory settings |
| `kubernetes` | K8sConfig | no | K8s Job configuration (namespace, resources, etc.) |
| `embedding` | EmbeddingConfig | yes | Embedding model and vector config |

**SourceConfig**:
```yaml
source:
  provider: gcs
  gcs:
    bucket: my-bucket
    source_prefix: ""
    expanded_prefix: expanded/
    cache_prefix: cache/
    queue_prefix: queue/
```

**DestConfig**:
```yaml
destination:
  provider: qdrant
  qdrant:
    url: http://localhost:6333       # overridable: QDRANT_URL
    api_key: ""                       # overridable: QDRANT_API_KEY
    timeout: 60
    batch_size: 100
```

**QueueConfig**:
```yaml
queue:
  batch_size: 1000
  lease_timeout: 600
```

**ProcessingConfig**:
```yaml
processing:
  max_file_size: 52428800       # 50 MB
  max_source_size: 10485760     # 10 MB
  docling_timeout: 600
  per_file_timeout: 600
  image_min_size: 51200         # 50 KB
  max_pages: 500
  retry_max: 3
  memory_threshold_mb: 4096    # 4 GB RSS
  malloc_arena_max: 2
```

**K8sConfig**:
```yaml
kubernetes:
  namespace: default            # default: auto-detect from pod
  service_account: ""           # default: auto-detect from pod
  image: ""                     # default: auto-detect from pod
  image_pull_policy: IfNotPresent
  runner_resources:
    requests:
      cpu: "500m"
      memory: "2Gi"
    limits:
      cpu: "2"
      memory: "4Gi"
  max_parallelism: 10
  node_selector: {}
  tolerations: []
  backoff_limit: 3
  ttl_seconds_after_finished: 3600
```

**EmbeddingConfig**:
```yaml
embedding:
  model: sentence-transformers/all-MiniLM-L6-v2
  vector_size: 384
  vector_name: fast-all-minilm-l6-v2
  max_tokens: 512
```

---

## Built-in File Type Groups (defaults.yaml)

| Group Name | Extensions | MIME Types | Detectors | Extractor | Chunker |
|-----------|-----------|-----------|-----------|-----------|---------|
| `office-documents` | .pdf, .docx, .xlsx, .pptx, .html, .htm, .rtf | application/pdf, application/vnd.openxmlformats | — | docling | docling-hybrid |
| `mumps-source` | .m, .ro | — | mumps-labels, caret-density | raw-text | mumps-label-boundary |
| `mumps-globals` | .zwr | — | caret-density | raw-text | mumps-label-boundary |
| `general-source` | .py, .js, .ts, .java, .c, .cpp, .h, .go, .rs, .rb, .php, .cs, .swift, .kt, .scala, .sh, .bash, .pl, .r, .sql, .lua, .zig, .v, .lisp, .clj, .ex, .erl, .hs | text/x-* | — | raw-text | chonkie-code (language: auto) |
| `data-files` | .json, .xml, .csv, .tsv, .yaml, .yml, .toml, .ini, .cfg, .conf, .properties | — | — | raw-text | chonkie-recursive |
| `images` | .png, .jpg, .jpeg, .gif, .bmp, .tiff, .svg | image/* | — | docling | docling-hybrid |
| `plain-text` | .txt, .md, .rst, .log, .readme | text/plain | — | raw-text | chonkie-recursive (recipe: markdown) |
| `binary` | — | application/octet-stream | — | — | — |

**Note**: The `binary` group is a catch-all for unclassifiable files. Files matching `binary` are skipped (not processed). The `images` group applies a minimum size threshold (FR-014, default 50 KB) before docling OCR.
