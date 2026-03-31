"""Runner main loop — claims batches, processes files one at a time."""

from __future__ import annotations

import logging
import random
import time

from thresher.config import Config
from thresher.controller.queue_builder import _serialize_batch, deserialize_batch
from thresher.embedder import MultiModelEmbedder
from thresher.processing.router import Router
from thresher.providers.destination import DestinationProvider
from thresher.providers.source import SourceProvider
from thresher.runner.memory_monitor import check_memory, gc_between_files
from thresher.runner.processor import FileProcessor
from thresher.types import ProcessingResult, ProcessingStatus, QueueBatch, QueueItem

logger = logging.getLogger("thresher.runner.loop")


class RunnerLoop:
    """Main runner loop: claim -> process -> mark."""

    def __init__(
        self,
        runner_id: str,
        source: SourceProvider,
        destination: DestinationProvider,
        embedder: MultiModelEmbedder,
        config: Config,
    ):
        self.runner_id = runner_id
        self.source = source
        self.destination = destination
        self.config = config

        router = Router(
            rules=config.routing.rules,
            default_collection=config.routing.default_collection,
            default_embedding=config.embedding.default,
        )

        self.processor = FileProcessor(
            source=source,
            destination=destination,
            embedder=embedder,
            router=router,
            config=config,
        )

        self.results: list[ProcessingResult] = []
        self._memory_exceeded = False
        self._last_reclaim_time: float = 0.0
        self._reclaim_interval: float = 300.0  # seconds between stale-batch scans

    def run(self) -> list[ProcessingResult]:
        """Run the main processing loop until no more batches available."""
        queue_prefix = self.config.source.gcs.queue_prefix

        logger.info("Runner %s starting", self.runner_id)

        while not self._memory_exceeded:
            # Reclaim stale batches periodically, not every iteration
            now = time.time()
            if now - self._last_reclaim_time >= self._reclaim_interval:
                self.reclaim_stale_batches(queue_prefix)
                self._last_reclaim_time = now

            batch_path = self._claim_next_batch(queue_prefix)
            if batch_path is None:
                logger.info("No more batches available")
                break

            self._process_batch(batch_path, queue_prefix)

        if self._memory_exceeded:
            logger.warning(
                "Runner %s exiting: memory threshold (%d MB) exceeded",
                self.runner_id,
                self.config.processing.memory_threshold_mb,
            )

        self._print_summary()
        return self.results

    # -- stale batch reclaim (T027) -----------------------------------------

    def reclaim_stale_batches(self, queue_prefix: str) -> int:
        """Move stale claimed batches back to pending. Returns count reclaimed."""
        claimed_prefix = f"{queue_prefix}claimed/"
        now = time.time()
        lease_timeout = self.config.queue.lease_timeout
        reclaimed = 0

        for file_info in self.source.list_files(prefix=claimed_prefix):
            try:
                data = self.source.download_content(file_info.path)
                batch = deserialize_batch(data.decode("utf-8"))

                claimed_at = batch.claimed_at or 0.0
                if claimed_at + lease_timeout >= now:
                    continue

                # Reset batch for re-processing
                batch.claimed_at = None
                batch.runner_id = None
                for item in batch.items:
                    if item.status == "processing":
                        item.status = "pending"

                pending_path = f"{queue_prefix}pending/{batch.batch_id}.json"
                pending_data = _serialize_batch(batch).encode("utf-8")
                try:
                    self.source.upload_content(pending_path, pending_data, if_generation_match=0)
                except FileExistsError:
                    # Another runner already reclaimed this batch
                    logger.debug("Batch %s already reclaimed by another runner", batch.batch_id)
                    continue
                self.source.delete(file_info.path)

                reclaimed += 1
                logger.info("Reclaimed stale batch %s", batch.batch_id)
            except Exception as exc:
                logger.warning("Error reclaiming %s: %s", file_info.path, exc)

        return reclaimed

    # -- claiming -----------------------------------------------------------

    def _claim_next_batch(self, queue_prefix: str) -> str | None:
        """Try to claim a pending batch via atomic conditional create."""
        pending_prefix = f"{queue_prefix}pending/"

        # List pending batches
        pending_files = list(self.source.list_files(prefix=pending_prefix))
        if not pending_files:
            return None

        # Shuffle to reduce contention between runners
        random.shuffle(pending_files)

        for file_info in pending_files:
            batch_name = file_info.path.split("/")[-1]
            claim_path = f"{queue_prefix}claimed/{self.runner_id}/{batch_name}"

            try:
                # Read the batch data
                data = self.source.download_content(file_info.path)

                # Try to claim via atomic conditional create
                batch = deserialize_batch(data.decode("utf-8"))
                batch.claimed_at = time.time()
                batch.runner_id = self.runner_id

                claim_data = _serialize_batch(batch).encode("utf-8")
                self.source.upload_content(claim_path, claim_data, if_generation_match=0)

                # Successfully claimed — delete from pending
                self.source.delete(file_info.path)

                logger.info("Claimed batch %s", batch_name)
                return claim_path

            except FileExistsError:
                # Another runner claimed it first
                continue
            except Exception as e:
                logger.warning("Error claiming batch %s: %s", batch_name, e)
                continue

        return None

    # -- batch processing (T026, T029, T030) --------------------------------

    def _process_batch(self, batch_path: str, queue_prefix: str) -> None:
        """Process all items in a claimed batch."""
        data = self.source.download_content(batch_path).decode("utf-8")
        batch = deserialize_batch(data)

        retry_items: list[QueueItem] = []
        failed_items: list[QueueItem] = []

        for item in batch.items:
            if self._memory_exceeded:
                break

            if item.status in ("complete", "permanently-failed"):
                continue

            item.status = "processing"
            item.attempt_count += 1

            result = self.processor.process_file(item.path, item.file_type_group)
            self.results.append(result)

            if result.status == ProcessingStatus.INDEXED:
                item.status = "complete"
                item.completed_at = time.time()
            elif result.status == ProcessingStatus.SKIPPED:
                item.status = "complete"
                item.completed_at = time.time()
            else:
                item.last_error = result.error_message
                if item.attempt_count >= self.config.processing.retry_max:
                    item.status = "permanently-failed"
                    failed_items.append(item)
                else:
                    item.status = "failed"
                    retry_items.append(item)

            # Memory check after each file
            gc_between_files()
            if check_memory(self.config.processing.memory_threshold_mb):
                self._memory_exceeded = True

        # Write retry batch
        if retry_items:
            self._write_sub_batch(
                queue_prefix, "retry", batch.batch_id, batch.created_at, retry_items
            )

        # Write permanently-failed batch
        if failed_items:
            self._write_sub_batch(
                queue_prefix, "failed", batch.batch_id, batch.created_at, failed_items
            )

        # Update skip list with completed items
        completed_paths = [item.path for item in batch.items if item.status == "complete"]
        if completed_paths:
            from thresher.controller.scanner import update_skip_list

            update_skip_list(self.source, queue_prefix, completed_paths)

        # Move batch to done
        done_path = f"{queue_prefix}done/{batch.batch_id}.json"
        done_data = _serialize_batch(batch).encode("utf-8")
        self.source.upload_content(done_path, done_data)
        self.source.delete(batch_path)

        logger.info("Completed batch %s", batch.batch_id)

    # -- helpers ------------------------------------------------------------

    def _write_sub_batch(
        self,
        queue_prefix: str,
        sub_dir: str,
        batch_id: str,
        created_at: float,
        items: list[QueueItem],
    ) -> None:
        """Write a list of QueueItems to queue/{sub_dir}/{batch_id}.json."""
        sub_batch = QueueBatch(
            batch_id=batch_id,
            created_at=created_at,
            item_count=len(items),
            items=items,
        )
        path = f"{queue_prefix}{sub_dir}/{batch_id}.json"
        self.source.upload_content(path, _serialize_batch(sub_batch).encode("utf-8"))
        logger.info("Wrote %d items to %s", len(items), path)

    # -- summary reporting (T031) -------------------------------------------

    def _print_summary(self) -> None:
        """Log a summary of processing results."""
        indexed = sum(1 for r in self.results if r.status == ProcessingStatus.INDEXED)
        skipped = sum(1 for r in self.results if r.status == ProcessingStatus.SKIPPED)
        failed = sum(1 for r in self.results if r.status == ProcessingStatus.FAILED)
        total_duration = sum(r.duration_seconds for r in self.results)

        logger.info(
            "Runner %s summary: %d indexed, %d skipped, %d failed, %.1fs total",
            self.runner_id,
            indexed,
            skipped,
            failed,
            total_duration,
        )
