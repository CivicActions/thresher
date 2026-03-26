# Contract: Configuration Schema

**Spec**: [../spec.md](../spec.md) | FR-026, FR-027, FR-029, FR-037, FR-040

## Full Configuration Schema (YAML)

```yaml
# ─── Source Provider ───────────────────────────────────────────────
source:
  provider: gcs                          # Provider name (required)
  gcs:                                   # GCS-specific settings
    bucket: ""                           # GCS bucket name (required)
    source_prefix: ""                    # Prefix for source files
    expanded_prefix: "expanded/"         # Prefix for expanded archive files
    cache_prefix: "cache/"               # Prefix for extraction cache
    queue_prefix: "queue/"               # Prefix for queue batches

# ─── Destination Provider ──────────────────────────────────────────
destination:
  provider: qdrant                       # Provider name (required)
  qdrant:                                # Qdrant-specific settings
    url: "http://localhost:6333"         # Qdrant URL (env: QDRANT_URL)
    api_key: ""                          # Qdrant API key (env: QDRANT_API_KEY)
    timeout: 60                          # Connection timeout seconds
    batch_size: 100                      # Upsert batch size

# ─── File Type Groups ─────────────────────────────────────────────
# User-defined groups merge with built-in defaults (defaults.yaml).
# Same-name groups replace the built-in entirely.
# Unmentioned built-in groups are preserved.
file_type_groups:
  # Example: override the built-in mumps-source group
  mumps-source:
    extensions: [".m", ".ro", ".RSA"]
    detectors: ["mumps-labels"]
    extractor: raw-text
    chunker:
      strategy: mumps-label-boundary
      chunk_size: 512

  # Example: add a custom group not in defaults
  audio-transcripts:
    extensions: [".mp3", ".wav", ".m4a"]
    extractor: docling
    chunker:
      strategy: docling-hybrid
      chunk_size: 1024

# ─── Routing Rules ────────────────────────────────────────────────
routing:
  default_collection: "vista"            # Collection for unmatched files
  source_suffix: "-source"               # Auto-suffix for source-code groups
  rules:
    - name: rpms-source
      file_group: ["mumps-source", "general-source"]
      path: ["rpms/"]
      collection: rpms-source

    - name: vista-docs
      file_group: ["office-documents", "plain-text"]
      path: ["vista/"]
      collection: vista

    - name: fallback-source
      file_group: ["mumps-source", "general-source", "mumps-globals"]
      collection: vista-source

# ─── Queue Settings ───────────────────────────────────────────────
queue:
  batch_size: 1000                       # Items per batch file
  lease_timeout: 600                     # Seconds before stale claim reclaim

# ─── Processing Settings ──────────────────────────────────────────
processing:
  max_file_size: 52428800                # 50 MB — skip larger files
  max_source_size: 10485760              # 10 MB — skip source code larger than this
  docling_timeout: 600                   # Subprocess timeout for docling conversion
  per_file_timeout: 600                  # Overall per-file processing timeout
  image_min_size: 51200                  # 50 KB — skip smaller images from OCR
  max_pages: 500                         # Max pages for docling extraction
  retry_max: 3                           # Max retry attempts per file
  memory_threshold_mb: 4096              # Runner RSS limit (4 GB)
  malloc_arena_max: 2                    # MALLOC_ARENA_MAX setting
  archive_depth: 2                       # Max recursive archive expansion depth
  summary_interval: 100                  # Log summary every N files

# ─── Embedding Settings ───────────────────────────────────────────
embedding:
  model: "sentence-transformers/all-MiniLM-L6-v2"
  vector_size: 384
  vector_name: "fast-all-minilm-l6-v2"
  max_tokens: 512                        # Max tokens per chunk (also used as tokenizer chunk_size)

# ─── Kubernetes Settings (optional) ───────────────────────────────
kubernetes:
  namespace: ""                          # Default: auto-detect from pod
  service_account: ""                    # Default: auto-detect from pod  
  image: ""                              # Default: auto-detect from pod
  image_pull_policy: "IfNotPresent"
  runner_resources:
    requests:
      cpu: "500m"
      memory: "2Gi"
    limits:
      cpu: "2"
      memory: "4Gi"
  max_parallelism: 10                    # Max concurrent runner Jobs
  node_selector: {}
  tolerations: []
  backoff_limit: 3                       # K8s-level retry
  ttl_seconds_after_finished: 3600       # Auto-delete Jobs after 1h
```

## Loading Order (FR-029)

```
1. Built-in defaults (src/defaults.yaml, shipped in package)
   ↓
2. User YAML config (path from CLI arg or THRESHER_CONFIG env var)
   ↓  merge: top-level sections shallow-merged, file_type_groups by name
3. Environment variable overrides (explicit mapping)
   ↓  patched into merged config
4. Validation → Config dataclass
```

## Environment Variable Overrides

| Env Var | Config Path | Description |
|---------|-------------|-------------|
| `QDRANT_URL` | `destination.qdrant.url` | Qdrant server URL |
| `QDRANT_API_KEY` | `destination.qdrant.api_key` | Qdrant API key |
| `GCS_BUCKET` | `source.gcs.bucket` | GCS bucket name |
| `THRESHER_CONFIG` | (CLI) | Path to user YAML config file |
| `MALLOC_ARENA_MAX` | `processing.malloc_arena_max` | glibc arena limit |

## File Type Group Schema

Each group under `file_type_groups` conforms to:

```yaml
<group-name>:                    # str: unique identifier
  extensions: [str]              # optional: file extensions (with leading dot)
  mime_types: [str]              # optional: MIME type prefixes
  detectors: [str]               # optional: custom content detector names
  priority: int                  # optional: classification priority (default: 100)
  extractor: str                 # required: "docling" or "raw-text"
  chunker:                       # required:
    strategy: str                # required: chunker strategy name
    chunk_size: int              # optional: max tokens (default from embedding.max_tokens)
    language: str                # optional: language hint (chonkie-code only)
    recipe: str                  # optional: splitting recipe (chonkie-recursive only)
```

## Routing Rule Schema

Each entry under `routing.rules` conforms to:

```yaml
- name: str                      # optional: human-readable name
  file_group: [str]              # optional: file type group names (ORed)
  path: [str]                    # optional: path patterns (ORed)
  filename: [str]                # optional: filename patterns (ORed)
  collection: str                # required: target collection
```

Matching: criteria types ANDed, values within each ORed, first-match-wins.
