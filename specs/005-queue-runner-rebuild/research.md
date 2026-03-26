# Research: Thresher

**Date**: 2025-03-25 | **Spec**: [spec.md](spec.md)

## R1: Chonkie Library Integration

### Decision
Use `chonkie[code]` package (v1.3+) for two chunking strategies: **CodeChunker** (tree-sitter AST-based, 165+ languages) and **RecursiveChunker** (plain text/markdown).

### Rationale
- CodeChunker provides semantic code splitting at AST node boundaries — far superior to character-based splitting for source code
- RecursiveChunker replaces the ad-hoc text-fallback chunker with a configurable, hierarchical splitting strategy
- Both chunkers accept a tokenizer string, enabling token-accurate chunk sizes using our embedding model (`sentence-transformers/all-MiniLM-L6-v2`)

### Key Integration Details

**Installation**: `pip install "chonkie[code]"` — installs tree-sitter, tree-sitter-language-pack, magika. Python ≥ 3.10.

**CodeChunker parameters**:
- `tokenizer`: str or tokenizer instance. Use `"sentence-transformers/all-MiniLM-L6-v2"` for token-accurate chunk sizes
- `chunk_size`: int, default 2048 — max tokens per chunk
- `language`: str or `"auto"`, default `"auto"` — **specify explicitly for performance** (auto uses Magika, slower)
- `include_nodes`: bool, default False — include AST node info

**RecursiveChunker parameters**:
- `tokenizer`: same as above
- `chunk_size`: int, default 2048
- `rules`: `RecursiveRules` — hierarchical splitting rules
- `min_characters_per_chunk`: int, default 24
- Factory method: `RecursiveChunker.from_recipe("markdown")` for Markdown-aware splitting

**Output format** (`Chunk` dataclass):
- `text`: str — the chunk text
- `start_index`: int — character offset in original string
- `end_index`: int — character offset in original string
- `token_count`: int — tokens in chunk

**No line number fields** — derive from character offsets: `text[:chunk.start_index].count('\n') + 1`

**No overlap parameter on CodeChunker** — use `OverlapRefinery` post-processor if needed.

**Language hint for file type groups**: File type group config should specify `language` explicitly (e.g., `python`, `java`, `javascript`). The `general-source` group can use `"auto"` as fallback.

### Alternatives Considered
- **Chonkie `QdrantHandshake`**: Auto-creates collections and uploads. Rejected — doesn't fit our provider abstraction architecture.
- **Chonkie `CHOMP` pipeline**: End-to-end pipeline. Rejected — we need fine-grained control per file type group.
- **Raw tree-sitter directly**: More control but requires manual chunk assembly. Chonkie wraps this correctly.
- **LangChain text splitters**: Heavier dependency, less code-aware splitting.

---

## R2: Kubernetes Python Client — Job Orchestration

### Decision
Use the `kubernetes` Python client library for programmatic K8s Job creation from the controller.

### Rationale
- Official client with full API coverage, well-maintained
- Supports in-cluster config (auto-discovery) and kubeconfig for local dev
- Typed model objects enable structured Job spec construction without raw YAML manipulation

### Key Integration Details

**Config loading** (in-cluster with local fallback):
```python
try:
    config.load_incluster_config()  # reads service account token, CA cert
except config.ConfigException:
    config.load_kube_config()       # reads ~/.kube/config
```

**Job creation**: `batch_v1_api.create_namespaced_job(namespace, body=job)` → returns `V1Job` with server-populated fields. Error: `ApiException` with `.status` (int) and `.reason` (str).

**Self-referencing image**: Best practice — inject own image as env var via Downward API or Helm value. Fallback: query own pod via K8s API using `MY_POD_NAME` env var (set via Downward API `metadata.name`).

**Namespace discovery**: Read `/var/run/secrets/kubernetes.io/serviceaccount/namespace` when in-cluster.

**Manifest export**: `client.ApiClient().sanitize_for_serialization(job)` → dict → `yaml.dump()`.

**Job best practices**:
- `restartPolicy: "Never"` (required for Jobs)
- `backoffLimit: 3` (k8s-level retry)
- `ttlSecondsAfterFinished: 3600` (auto-cleanup)
- `activeDeadlineSeconds` for overall timeout
- Labels: `app`, `job-type`, `batch-id` for tracking

**Credentials**: Use `V1EnvVar(value_from=V1EnvVarSource(secret_key_ref=...))` for K8s Secrets.

### Alternatives Considered
- **Shell out to `kubectl`**: Rejected — no structured error handling, requires kubectl binary in image.
- **Helm SDK**: Rejected — overkill for Job creation, adds Go/CGo dependency complexity.
- **Raw HTTP to K8s API**: Rejected — reimplements what the client library does, error-prone auth handling.

---

## R3: YAML Configuration with Layered Defaults

### Decision
Use PyYAML with `safe_load`, `importlib.resources` for package defaults, shallow dict merge for user overrides, and explicit env var mapping for sensitive values.

### Rationale
- PyYAML is lightweight (~100KB), sufficient for read-only config loading
- `importlib.resources.files()` is the stdlib approach for Python 3.11+ package resources
- Shallow merge at group-name level matches the "override per-group" semantic exactly
- Explicit env var mapping is safer and more debuggable than template interpolation

### Key Integration Details

**Loading package defaults** (Python 3.11+):
```python
from importlib.resources import files
defaults_path = files("thresher") / "defaults.yaml"
defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
```

Declare in `pyproject.toml`:
```toml
[tool.setuptools.package-data]
thresher = ["defaults.yaml"]
```

**Merge strategy** — one level of dict merge per top-level section:
- `file_type_groups`: `{**defaults["file_type_groups"], **user["file_type_groups"]}` — user groups replace by name, unmentioned preserved
- Other sections: same pattern — user keys override, default keys preserved
- Scalars/lists: user wins entirely

**Environment variable overrides** — explicit mapping, applied post-merge:
```python
ENV_OVERRIDES = {
    "QDRANT_URL": "destination.qdrant.url",
    "QDRANT_API_KEY": "destination.qdrant.api_key",
    "GCS_BUCKET": "source.gcs.bucket",
}
```

**Loading order**: built-in defaults.yaml → merge(user config.yaml) → apply env overrides → validate → Config object

**Validation**: Stick with dataclasses (current approach). No need for Pydantic — config schema is stable and manually validated during construction.

**PyYAML YAML 1.1 gotcha**: `yes`/`no`/`on`/`off` interpreted as booleans. Not a practical concern for this config format (values are paths, numbers, lists). Document in defaults.yaml to always use `true`/`false`.

### Alternatives Considered
- **Pydantic v2 for validation**: Rejected — adds ~2MB compiled dependency; dataclasses are sufficient for stable config schema.
- **ruamel.yaml**: Rejected — YAML 1.2 compliance and round-trip editing not needed; PyYAML simpler.
- **OmegaConf/Hydra**: Rejected — heavy dependency, brings unwanted config composition complexity.
- **Template-based env interpolation** (`${QDRANT_URL}` in YAML): Rejected — can produce invalid YAML, couples defaults to env var names, injection risk.
- **TOML (current)**: Rejected by spec requirement — YAML better supports hierarchical file type group definitions.

---

## R4: GCS Atomic Queue Claiming

### Decision
Use GCS `if_generation_match=0` (create-only) for atomic batch claiming, with upload-then-delete pattern and idempotent stale recovery.

### Rationale
- GCS `if_generation_match=0` provides server-side atomic create-only semantics — no TOCTOU races possible
- Upload-then-delete is simpler and more reliable than rename (which is copy+delete, not atomic in GCS)
- GCS is strongly consistent for all operations since Feb 2021 — list-after-write is immediate

### Key Integration Details

**Claim pattern**:
1. Runner writes claim file to `queue/claimed/{runner_id}/{batch_name}.json` with `if_generation_match=0`
2. On success (exclusive claim), delete from `queue/pending/`
3. On `PreconditionFailed` (HTTP 412), another runner claimed it — skip and try next

**Exception**: `google.api_core.exceptions.PreconditionFailed` on conflict. `google.api_core.exceptions.NotFound` on delete of already-deleted file (safe to ignore).

**Stale recovery**: Scan `queue/claimed/`, check `claimed_at + lease_timeout < now`, move stale batches back to `queue/pending/` (also using `if_generation_match=0` for idempotency).

**Contention reduction**: Shuffle pending batch list before attempting claims — reduces multiple runners converging on same batch.

**Batch completion**: Move from `queue/claimed/{runner_id}/` to `queue/done/` via upload-then-delete.

**All operations are idempotent**: crash at any point → recovery pass cleans up.

### Alternatives Considered
- **GCS `blob.rename()`**: Rejected — implemented as copy+delete (not atomic), no create-only semantic on destination.
- **GCS compose**: Not applicable to claiming semantics.
- **Redis/Pub/Sub queue**: Rejected — adds infrastructure dependency; GCS batch files scale to 500K+ with zero contention.
- **Database-backed queue**: Rejected — unnecessary for batch-oriented processing; GCS is already the source provider.

---

## R5: Docling Subprocess Isolation

### Decision
Preserve the existing `subprocess.Popen` pattern for docling conversions (already implemented in `src/extractor.py`).

### Rationale
- Current implementation correctly uses `subprocess.Popen` (not `ProcessPoolExecutor`) to avoid fork-based memory duplication
- `posix_spawn`/`vfork+exec` ensures native memory from libpdfium, ONNX runtime, and PyTorch is fully reclaimed by OS when child exits
- Configurable timeout (600s default) kills pathological files

### Key Integration Details

The existing pattern from `src/extractor.py` will be preserved and wrapped behind the docling extractor interface:
- Worker script is written inline and executed as a separate Python process
- stdout/stderr to DEVNULL, communication via temp file (serialized DoclingDocument)
- Timeout enforced via `proc.wait(timeout=...)` with `proc.kill()` on timeout

**New in rebuild**: The extractor is selected by file type group config (`extractor: docling` or `extractor: raw-text`), not by hardcoded MIME type dispatch.

### Alternatives Considered
- **ProcessPoolExecutor**: Rejected — uses `fork()` which duplicates parent memory space, defeating the purpose of isolation.
- **In-process docling**: Rejected — native memory leaks accumulate across files, causing OOM.
- **Docker-in-Docker per file**: Rejected — excessive overhead for single file conversion.
