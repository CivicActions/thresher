"""Expansion orchestrator — coordinates parallel archive expansion."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from thresher.config import Config
from thresher.controller.archive_expander import ArchiveExpander
from thresher.providers.source import SourceProvider
from thresher.types import ExpansionResult, FileInfo

logger = logging.getLogger("thresher.controller.expansion_orchestrator")


class ExpansionOrchestrator:
    """Orchestrates parallel archive expansion via local threads or K8s Jobs."""

    def __init__(self, config: Config, source: SourceProvider) -> None:
        self._config = config
        self._source = source
        self._max_parallelism = config.processing.max_expansion_parallelism
        self._timeout = config.processing.expansion_timeout

    def _make_expander(self) -> ArchiveExpander:
        return ArchiveExpander(
            source=self._source,
            expanded_prefix=self._config.source.gcs.expanded_prefix,
            max_depth=self._config.processing.archive_depth,
            exclude_extensions=self._config.processing.archive_exclude_extensions,
            upload_batch_size=self._config.processing.upload_batch_size,
        )

    def _is_already_expanded(self, archive_path: str) -> bool:
        """Check if an expansion record already exists for this archive."""
        expander = self._make_expander()
        return expander._load_expansion_record(archive_path) is not None

    def _filter_unexpanded(self, archives: list[FileInfo]) -> tuple[list[FileInfo], int]:
        """Partition archives into those needing expansion and already-expanded count."""
        to_expand: list[FileInfo] = []
        skipped = 0
        for fi in archives:
            if self._is_already_expanded(fi.path):
                skipped += 1
            else:
                to_expand.append(fi)
        if skipped:
            logger.info("Skipping %d already-expanded archive(s)", skipped)
        return to_expand, skipped

    # ------------------------------------------------------------------
    # Local mode
    # ------------------------------------------------------------------

    def expand_local(self, archives: list[FileInfo]) -> ExpansionResult:
        """Expand archives locally using a thread pool."""
        start = time.monotonic()

        to_expand, skipped = self._filter_unexpanded(archives)
        if not to_expand:
            return ExpansionResult(
                archives_expanded=skipped,
                archives_failed=0,
                files_extracted=0,
                duration_seconds=time.monotonic() - start,
            )

        logger.info(
            "Expanding %d archive(s) locally (max_parallelism=%d)",
            len(to_expand),
            self._max_parallelism,
        )

        expanded = 0
        failed = 0
        files_extracted = 0
        failed_archives: list[str] = []

        def _expand_one(fi: FileInfo) -> tuple[str, int, str | None]:
            """Expand a single archive, return (path, member_count, error)."""
            try:
                expander = self._make_expander()
                results = expander._expand_single(fi.path, depth=0)
                return fi.path, len(results), None
            except Exception as e:
                logger.error("Failed to expand %s: %s", fi.path, e)
                return fi.path, 0, str(e)

        with ThreadPoolExecutor(max_workers=self._max_parallelism) as pool:
            futures = {pool.submit(_expand_one, fi): fi for fi in to_expand}
            for future in as_completed(futures):
                path, count, error = future.result()
                if error:
                    failed += 1
                    failed_archives.append(path)
                else:
                    expanded += 1
                    files_extracted += count

        duration = time.monotonic() - start
        logger.info(
            "Expansion complete: %d expanded, %d failed, %d files extracted (%.1fs)",
            expanded + skipped,
            failed,
            files_extracted,
            duration,
        )

        return ExpansionResult(
            archives_expanded=expanded + skipped,
            archives_failed=failed,
            files_extracted=files_extracted,
            duration_seconds=duration,
            failed_archives=failed_archives,
        )

    # ------------------------------------------------------------------
    # K8s mode
    # ------------------------------------------------------------------

    def expand_k8s(self, archives: list[FileInfo]) -> ExpansionResult:
        """Deploy K8s expansion jobs and wait for completion."""
        start = time.monotonic()

        to_expand, skipped = self._filter_unexpanded(archives)
        if not to_expand:
            return ExpansionResult(
                archives_expanded=skipped,
                archives_failed=0,
                files_extracted=0,
                duration_seconds=time.monotonic() - start,
            )

        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        archive_paths = [fi.path for fi in to_expand]
        orchestrator = K8sOrchestrator(self._config, [])
        job_specs = orchestrator.build_expansion_job_specs(archive_paths)

        # Deploy jobs in waves respecting max_parallelism
        try:
            from kubernetes import client
            from kubernetes import config as k8s_config
        except ImportError:
            raise RuntimeError(
                "kubernetes package required for K8s expansion. "
                "Install with: pip install kubernetes"
            )

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        batch_api = client.BatchV1Api()
        namespace = orchestrator.detect_namespace()

        created_jobs: list[str] = []
        for spec in job_specs[: self._max_parallelism]:
            job_name = spec["metadata"]["name"]
            try:
                batch_api.create_namespaced_job(namespace=namespace, body=spec)
                created_jobs.append(job_name)
                logger.info("Created expansion Job: %s", job_name)
            except Exception as e:
                logger.error("Failed to create expansion Job %s: %s", job_name, e)

        logger.info(
            "Deploying %d expansion jobs (max_parallelism=%d)",
            len(created_jobs),
            self._max_parallelism,
        )

        # Poll for completion
        expanded = 0
        failed = 0
        files_extracted = 0
        failed_archives: list[str] = []
        poll_interval = 10

        remaining = dict(zip(created_jobs, to_expand[: len(created_jobs)]))
        pending_specs = job_specs[self._max_parallelism :]
        pending_archives = to_expand[self._max_parallelism :]

        while remaining:
            elapsed = time.monotonic() - start
            if elapsed > self._timeout:
                still_running = [fi.path for fi in remaining.values()]
                not_started = [fi.path for fi in pending_archives]
                logger.error(
                    "Expansion timeout after %.0fs: %d jobs still running, "
                    "%d archives never started. In-flight jobs will continue "
                    "in K8s and be picked up on re-run.",
                    elapsed,
                    len(still_running),
                    len(not_started),
                )
                break

            time.sleep(poll_interval)
            completed_jobs: list[str] = []

            for job_name, fi in list(remaining.items()):
                try:
                    job = batch_api.read_namespaced_job(name=job_name, namespace=namespace)
                    if job.status.succeeded and job.status.succeeded > 0:
                        completed_jobs.append(job_name)
                        expanded += 1
                        record = self._make_expander()._load_expansion_record(fi.path)
                        if record:
                            files_extracted += record.member_count
                    elif job.status.failed and job.status.failed > 0:
                        completed_jobs.append(job_name)
                        failed += 1
                        failed_archives.append(fi.path)
                        logger.warning("Expansion job %s failed for %s", job_name, fi.path)
                except Exception as e:
                    logger.debug("Error checking job %s: %s", job_name, e)

            for job_name in completed_jobs:
                del remaining[job_name]

                # Launch next pending job if available
                if pending_specs:
                    next_spec = pending_specs.pop(0)
                    next_fi = pending_archives.pop(0)
                    next_name = next_spec["metadata"]["name"]
                    try:
                        batch_api.create_namespaced_job(namespace=namespace, body=next_spec)
                        remaining[next_name] = next_fi
                        logger.info("Created expansion Job: %s", next_name)
                    except Exception as e:
                        logger.error("Failed to create expansion Job %s: %s", next_name, e)
                        failed += 1
                        failed_archives.append(next_fi.path)

            if remaining:
                logger.info(
                    "Expansion progress: %d/%d complete, %d failed",
                    expanded + failed,
                    len(to_expand),
                    failed,
                )

        duration = time.monotonic() - start
        logger.info(
            "Expansion complete: %d expanded, %d failed, %d files extracted (%.1fs)",
            expanded + skipped,
            failed,
            files_extracted,
            duration,
        )

        return ExpansionResult(
            archives_expanded=expanded + skipped,
            archives_failed=failed,
            files_extracted=files_extracted,
            duration_seconds=duration,
            failed_archives=failed_archives,
        )
