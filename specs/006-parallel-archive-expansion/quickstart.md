# Quickstart: Parallel Archive Expansion

## Local Mode (no K8s required)

```bash
# 1. Ensure archives are in your GCS source bucket
# 2. Run controller with --local (expansion runs in-process with thread pool)
uv run thresher controller --config config.yaml --local
```

The controller will:
1. Scan source files, detect archives
2. Expand archives in parallel (up to `max_expansion_parallelism` concurrent)
3. Upload extracted files with concurrent batches (`upload_batch_size`)
4. Build processing queue batches (direct + expanded files)
5. Process all files via embedded runner

## K8s Mode

```bash
# 1. Run controller to deploy expansion jobs first, then runner jobs
uv run thresher controller --config config.yaml --k8s-deploy
```

The controller will:
1. Scan source files, detect archives
2. Deploy expansion K8s Jobs (one per archive, up to `max_expansion_parallelism` concurrent)
3. Wait for all expansion jobs to complete
4. Rescan expanded files
5. Build processing queue batches
6. Deploy runner K8s Jobs

## Configuration

Add to your `config.yaml`:

```yaml
processing:
  max_expansion_parallelism: 5    # Concurrent expansion jobs/threads
  upload_batch_size: 50           # Concurrent uploads per expansion job
  expansion_timeout: 3600         # Max wait time for expansion phase (seconds)
```

## Manual Expansion (single archive)

```bash
# Expand a single archive directly (used by K8s expansion jobs)
uv run thresher expander --config config.yaml --archive-path source/data.zip
```

## Monitoring

Watch expansion progress in controller logs:

```
INFO  Deploying 25 expansion jobs (max_parallelism=5)
INFO  Expansion progress: 10/25 complete, 0 failed
INFO  Expansion progress: 20/25 complete, 1 failed
INFO  Expansion complete: 24 expanded, 1 failed, 15234 files extracted (142s)
WARN  Failed archives: source/corrupt.zip
```
