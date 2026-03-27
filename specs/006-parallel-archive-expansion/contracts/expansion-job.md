# Contract: Expansion Job CLI

## Command

```
thresher expander --config CONFIG --archive-path ARCHIVE_PATH [--force]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--config` | Yes | Path to YAML config file |
| `--archive-path` | Yes | GCS path of the archive to expand (e.g., `source/data.zip`) |
| `--force` | No | Re-expand even if expansion record exists |

## Behavior

1. Load config (same three-layer merge as controller/runner)
2. Create SourceProvider from config
3. Check for existing expansion record at `{expanded_prefix}/{archive_stem}/.expansion-record.json`
   - If exists and `--force` not set: log skip, exit 0
4. Download archive to temp directory
5. Extract archive members (respecting `archive_depth`, `archive_exclude_extensions`)
6. Upload members concurrently using `upload_batch_size` workers
7. Handle nested archives (recursive extraction up to depth limit)
8. Write expansion record on success
9. Exit 0 on success, exit 1 on failure

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (or skipped due to existing record) |
| 1 | Failure (archive corrupt, download failed, upload failed after retries) |

## Environment Variables

Inherits standard environment: `GCS_BUCKET`, `QDRANT_URL` (unused but present), plus any K8s-injected config.

## K8s Job Spec

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: thresher-expander-{archive_stem}
  labels:
    app: thresher
    component: expander
spec:
  backoffLimit: 1
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: expander
          image: {same as runner image}
          args: ["expander", "--config", "/config/config.yaml", "--archive-path", "{archive_path}"]
          resources: {same as runner resources}
```

## Completion Signal

Expansion record written to `{expanded_prefix}/{archive_stem}/.expansion-record.json`. Controller polls for this file to detect completion. K8s Job status (Succeeded/Failed) provides failure detection for jobs that crash before writing a record.
