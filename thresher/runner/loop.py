"""Runner main loop — claims batches, processes files one at a time."""

from __future__ import annotations

import logging
import random
import time

from thresher.config import Config
from thresher.controller.queue_builder import _serialize_batch, deserialize_batch
from thresher.embedder import Embedder
from thresher.processing.router import Router
from thresher.providers.destination import DestinationProvider
from thresher.providers.source import SourceProvider
from thresher.runner.processor import FileProcessor
from thresher.types import ProcessingResult, ProcessingStatus

logger = logging.getLogger("thresher.runner.loop")


class RunnerLoop:
    """Main runner loop: claim -> process -> mark."""

    def __init__(
        self,
        runner_id: str,
        source: SourceProvider,
        destination: DestinationProvider,
        embedder: Embedder,
        config: Config,
    ):
        self.runner_id = runner_id
        self.source = source
        self.destination = destination
        self.config = config

        router = Router(
            rules=config.routing.rules,
            default_collection=config.routing.default_collection,
            source_suffix=config.routing.source_suffix,
        )

        self.processor = FileProcessor(
            source=source,
            destination=destination,
            embedder=embedder,
            router=router,
            config=config,
        )

        self.results: list[ProcessingResult] = []

    def run(self) -> list[ProcessingResult]:
        """Run the main processing loop until no more batches available."""
        queue_prefix = self.config.source.gcs.queue_prefix

        logger.info("Runner %s starting", self.runner_id)

        while True:
            batch_path = self._claim_next_batch(queue_prefix)
            if batch_path is None:
                logger.info("No more batches available")
                break

            self._process_batch(batch_path, queue_prefix)

        logger.info(
            "Runner %s finished: %d files processed",
            self.runner_id,
            len(self.results),
        )
        return self.results

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

    def _process_batch(self, batch_path: str, queue_prefix: str) -> None:
        """Process all items in a claimed batch."""
        data = self.source.download_content(batch_path).decode("utf-8")
        batch = deserialize_batch(data)

        for item in batch.items:
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
                item.status = "failed"
                item.last_error = result.error_message

        # Move batch to done
        done_path = f"{queue_prefix}done/{batch.batch_id}.json"
        done_data = _serialize_batch(batch).encode("utf-8")
        self.source.upload_content(done_path, done_data)
        self.source.delete(batch_path)

        logger.info("Completed batch %s", batch.batch_id)
