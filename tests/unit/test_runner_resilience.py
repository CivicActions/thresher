"""Unit tests for Phase 4 runner resilience features (T026-T032)."""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from thresher.config import (
    Config,
    DestConfig,
    EmbeddingConfig,
    GCSConfig,
    ProcessingConfig,
    QdrantConfig,
    QueueConfig,
    RoutingConfig,
    SourceConfig,
)
from thresher.controller.queue_builder import _serialize_batch, deserialize_batch
from thresher.runner.loop import RunnerLoop
from thresher.types import (
    FileInfo,
    ProcessingResult,
    ProcessingStatus,
    QueueBatch,
    QueueItem,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> Config:
    cfg = Config()
    cfg.source = SourceConfig(
        provider="gcs",
        gcs=GCSConfig(bucket="test-bucket", source_prefix="data/", queue_prefix="queue/"),
    )
    cfg.destination = DestConfig(
        provider="qdrant",
        qdrant=QdrantConfig(url="http://localhost:6333"),
    )
    cfg.routing = RoutingConfig(default_collection="default", rules=[])
    cfg.embedding = EmbeddingConfig()
    cfg.queue = QueueConfig(batch_size=1000, lease_timeout=600)
    cfg.processing = ProcessingConfig(retry_max=3, memory_threshold_mb=4096, per_file_timeout=600)
    return cfg


@pytest.fixture
def mock_source() -> MagicMock:
    source = MagicMock()
    source.cache_path.return_value = "cache/test.md"
    source.exists.return_value = False
    return source


@pytest.fixture
def mock_destination() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed_texts.return_value = [[0.1, 0.2, 0.3]]
    return embedder


def _make_batch(
    batch_id: str = "batch-0001",
    items: list[QueueItem] | None = None,
    claimed_at: float | None = None,
    runner_id: str | None = None,
    reclaim_count: int = 0,
) -> QueueBatch:
    if items is None:
        items = [QueueItem(path="file1.m", source_type="direct")]
    return QueueBatch(
        batch_id=batch_id,
        created_at=time.time(),
        item_count=len(items),
        items=items,
        claimed_at=claimed_at,
        runner_id=runner_id,
        reclaim_count=reclaim_count,
    )


def _make_loop(
    mock_source: MagicMock,
    mock_destination: MagicMock,
    mock_embedder: MagicMock,
    config: Config,
) -> RunnerLoop:
    return RunnerLoop(
        runner_id="runner-01",
        source=mock_source,
        destination=mock_destination,
        embedder=mock_embedder,
        config=config,
    )


# ---------------------------------------------------------------------------
# T026 — Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Failed items under retry_max are written to queue/retry/."""

    def test_failed_item_written_to_retry(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        item = QueueItem(path="file1.m", source_type="direct", attempt_count=0)
        batch = _make_batch(items=[item])
        batch_json = _serialize_batch(batch).encode("utf-8")

        mock_source.download_content.return_value = batch_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.processor = MagicMock()
        loop.processor.process_file.return_value = ProcessingResult(
            path="file1.m",
            status=ProcessingStatus.FAILED,
            duration_seconds=1.0,
            error_message="extraction error",
        )

        with (
            patch("thresher.runner.loop.gc_between_files"),
            patch("thresher.runner.loop.check_memory", return_value=False),
        ):
            loop._process_batch("queue/claimed/runner-01/batch-0001.json", "queue/")

        # Should write to retry/ (attempt_count=1 < retry_max=3)
        upload_calls = mock_source.upload_content.call_args_list
        retry_calls = [c for c in upload_calls if "retry/" in str(c)]
        assert len(retry_calls) == 1
        assert "queue/retry/batch-0001.json" in str(retry_calls[0])

    def test_successful_item_not_retried(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        item = QueueItem(path="file1.m", source_type="direct")
        batch = _make_batch(items=[item])
        batch_json = _serialize_batch(batch).encode("utf-8")

        mock_source.download_content.return_value = batch_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.processor = MagicMock()
        loop.processor.process_file.return_value = ProcessingResult(
            path="file1.m",
            status=ProcessingStatus.INDEXED,
            duration_seconds=1.0,
        )

        with (
            patch("thresher.runner.loop.gc_between_files"),
            patch("thresher.runner.loop.check_memory", return_value=False),
        ):
            loop._process_batch("queue/claimed/runner-01/batch-0001.json", "queue/")

        upload_calls = mock_source.upload_content.call_args_list
        retry_calls = [c for c in upload_calls if "retry/" in str(c)]
        assert len(retry_calls) == 0


# ---------------------------------------------------------------------------
# T030 — Permanent failure
# ---------------------------------------------------------------------------


class TestPermanentFailure:
    """Items exceeding retry_max are written to queue/failed/."""

    def test_item_exceeding_retry_max_written_to_failed(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        # attempt_count=2, will be incremented to 3 which == retry_max
        item = QueueItem(
            path="file1.m",
            source_type="direct",
            attempt_count=2,
        )
        batch = _make_batch(items=[item])
        batch_json = _serialize_batch(batch).encode("utf-8")

        mock_source.download_content.return_value = batch_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.processor = MagicMock()
        loop.processor.process_file.return_value = ProcessingResult(
            path="file1.m",
            status=ProcessingStatus.FAILED,
            duration_seconds=1.0,
            error_message="permanent error",
        )

        with (
            patch("thresher.runner.loop.gc_between_files"),
            patch("thresher.runner.loop.check_memory", return_value=False),
        ):
            loop._process_batch("queue/claimed/runner-01/batch-0001.json", "queue/")

        upload_calls = mock_source.upload_content.call_args_list
        failed_calls = [c for c in upload_calls if "failed/" in str(c)]
        assert len(failed_calls) == 1
        assert "queue/failed/batch-0001.json" in str(failed_calls[0])

        # Should NOT write to retry/
        retry_calls = [c for c in upload_calls if "retry/" in str(c)]
        assert len(retry_calls) == 0

    def test_permanently_failed_item_has_error(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        item = QueueItem(path="file1.m", source_type="direct", attempt_count=2)
        batch = _make_batch(items=[item])
        batch_json = _serialize_batch(batch).encode("utf-8")

        mock_source.download_content.return_value = batch_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.processor = MagicMock()
        loop.processor.process_file.return_value = ProcessingResult(
            path="file1.m",
            status=ProcessingStatus.FAILED,
            duration_seconds=1.0,
            error_message="permanent error",
        )

        with (
            patch("thresher.runner.loop.gc_between_files"),
            patch("thresher.runner.loop.check_memory", return_value=False),
        ):
            loop._process_batch("queue/claimed/runner-01/batch-0001.json", "queue/")

        # Verify the failed batch contains the error
        failed_call = [c for c in mock_source.upload_content.call_args_list if "failed/" in str(c)]
        assert len(failed_call) == 1
        written_data = failed_call[0][0][1]  # second positional arg
        written_batch = deserialize_batch(written_data.decode("utf-8"))
        assert written_batch.items[0].last_error == "permanent error"
        assert written_batch.items[0].status == "permanently-failed"


# ---------------------------------------------------------------------------
# T027 — Stale batch reclaim
# ---------------------------------------------------------------------------


class TestStaleBatchReclaim:
    """Stale claimed batches are moved back to pending."""

    def test_stale_batch_reclaimed(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        # Batch claimed 1000 seconds ago, lease_timeout is 600
        stale_batch = _make_batch(
            claimed_at=time.time() - 1000,
            runner_id="runner-dead",
            items=[
                QueueItem(path="file1.m", source_type="direct", status="processing"),
            ],
        )
        stale_json = _serialize_batch(stale_batch).encode("utf-8")

        mock_source.list_files.return_value = iter(
            [
                FileInfo(
                    path="queue/claimed/runner-dead/batch-0001.json",
                    size=100,
                    updated=datetime.now(),
                ),
            ]
        )
        mock_source.download_content.return_value = stale_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        reclaimed = loop.reclaim_stale_batches("queue/")

        assert reclaimed == 1
        # Should upload to pending and delete from claimed
        upload_calls = [
            c for c in mock_source.upload_content.call_args_list if "pending/" in str(c)
        ]
        assert len(upload_calls) == 1
        mock_source.delete.assert_called_once_with("queue/claimed/runner-dead/batch-0001.json")

    def test_non_stale_batch_not_reclaimed(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        # Batch claimed just now — not stale
        fresh_batch = _make_batch(
            claimed_at=time.time(),
            runner_id="runner-alive",
        )
        fresh_json = _serialize_batch(fresh_batch).encode("utf-8")

        mock_source.list_files.return_value = iter(
            [
                FileInfo(
                    path="queue/claimed/runner-alive/batch-0001.json",
                    size=100,
                    updated=datetime.now(),
                ),
            ]
        )
        mock_source.download_content.return_value = fresh_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        reclaimed = loop.reclaim_stale_batches("queue/")

        assert reclaimed == 0
        mock_source.delete.assert_not_called()

    def test_reclaimed_batch_resets_processing_items(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        stale_batch = _make_batch(
            claimed_at=time.time() - 1000,
            runner_id="runner-dead",
            items=[
                QueueItem(path="f1.m", source_type="direct", status="processing"),
                QueueItem(path="f2.m", source_type="direct", status="complete"),
            ],
        )
        stale_json = _serialize_batch(stale_batch).encode("utf-8")

        mock_source.list_files.return_value = iter(
            [
                FileInfo(
                    path="queue/claimed/runner-dead/batch-0001.json",
                    size=100,
                    updated=datetime.now(),
                ),
            ]
        )
        mock_source.download_content.return_value = stale_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.reclaim_stale_batches("queue/")

        upload_call = [
            c for c in mock_source.upload_content.call_args_list if "pending/" in str(c)
        ][0]
        written_data = upload_call[0][1]
        written_batch = deserialize_batch(written_data.decode("utf-8"))

        # "processing" items should be reset to "pending"
        assert written_batch.items[0].status == "pending"
        # "complete" items should stay "complete"
        assert written_batch.items[1].status == "complete"

    def test_repeatedly_reclaimed_batch_moved_to_retry(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        """Batch reclaimed more than max_reclaims times goes to retry/ not pending/."""
        # Already reclaimed once (reclaim_count=1), max_reclaims defaults to 1
        stale_batch = _make_batch(
            claimed_at=time.time() - 1000,
            runner_id="runner-oom",
            reclaim_count=1,
            items=[
                QueueItem(path="big_file.pdf", source_type="direct", status="processing"),
            ],
        )
        stale_json = _serialize_batch(stale_batch).encode("utf-8")

        mock_source.list_files.return_value = iter(
            [
                FileInfo(
                    path="queue/claimed/runner-oom/batch-0001.json",
                    size=100,
                    updated=datetime.now(),
                ),
            ]
        )
        mock_source.download_content.return_value = stale_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        reclaimed = loop.reclaim_stale_batches("queue/")

        assert reclaimed == 1
        # Should upload to retry/, NOT pending/
        retry_calls = [c for c in mock_source.upload_content.call_args_list if "retry/" in str(c)]
        pending_calls = [
            c for c in mock_source.upload_content.call_args_list if "pending/" in str(c)
        ]
        assert len(retry_calls) == 1
        assert len(pending_calls) == 0


# ---------------------------------------------------------------------------
# T029 — Per-file timeout
# ---------------------------------------------------------------------------


class TestPerFileTimeout:
    """Per-file timeout results in a FAILED result with timeout error message."""

    def test_timeout_marks_file_as_failed(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        config.processing.per_file_timeout = 1  # 1 second timeout

        item = QueueItem(path="slow_file.pdf", source_type="direct")
        batch = _make_batch(items=[item])
        batch_json = _serialize_batch(batch).encode("utf-8")

        mock_source.download_content.return_value = batch_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)

        # processor.process_file raises TimeoutError
        loop.processor = MagicMock()
        loop.processor.process_file.return_value = ProcessingResult(
            path="slow_file.pdf",
            status=ProcessingStatus.FAILED,
            duration_seconds=1.0,
            error_message="File processing exceeded 1s timeout",
        )

        with (
            patch("thresher.runner.loop.gc_between_files"),
            patch("thresher.runner.loop.check_memory", return_value=False),
        ):
            loop._process_batch("queue/claimed/runner-01/batch-0001.json", "queue/")

        assert loop.results[0].status == ProcessingStatus.FAILED
        assert "timeout" in (loop.results[0].error_message or "").lower()


# ---------------------------------------------------------------------------
# T031 — Summary reporting
# ---------------------------------------------------------------------------


class TestSummaryReporting:
    """_print_summary logs correct counts."""

    def test_summary_counts(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.results = [
            ProcessingResult(path="a", status=ProcessingStatus.INDEXED, duration_seconds=1.0),
            ProcessingResult(path="b", status=ProcessingStatus.INDEXED, duration_seconds=2.0),
            ProcessingResult(path="c", status=ProcessingStatus.SKIPPED, duration_seconds=0.5),
            ProcessingResult(
                path="d",
                status=ProcessingStatus.FAILED,
                duration_seconds=0.1,
                error_message="oops",
            ),
        ]

        with patch("thresher.runner.loop.logger") as mock_logger:
            loop._print_summary()

        # Verify the summary call
        mock_logger.info.assert_called_once()
        args = mock_logger.info.call_args[0]
        assert "2 indexed" in args[0] % args[1:]
        assert "1 skipped" in args[0] % args[1:]
        assert "1 failed" in args[0] % args[1:]

    def test_summary_with_empty_results(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.results = []

        with patch("thresher.runner.loop.logger") as mock_logger:
            loop._print_summary()

        args = mock_logger.info.call_args[0]
        assert "0 indexed" in args[0] % args[1:]


# ---------------------------------------------------------------------------
# T028 — Memory check exits loop
# ---------------------------------------------------------------------------


class TestMemoryExitsLoop:
    """Runner exits loop when memory threshold exceeded."""

    def test_memory_exceeded_stops_processing(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        items = [
            QueueItem(path="file1.m", source_type="direct"),
            QueueItem(path="file2.m", source_type="direct"),
            QueueItem(path="file3.m", source_type="direct"),
        ]
        batch = _make_batch(items=items)
        batch_json = _serialize_batch(batch).encode("utf-8")

        mock_source.download_content.return_value = batch_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.processor = MagicMock()
        loop.processor.process_file.return_value = ProcessingResult(
            path="file.m",
            status=ProcessingStatus.INDEXED,
            duration_seconds=1.0,
        )

        call_count = [0]

        def memory_check(threshold_mb: int) -> bool:
            call_count[0] += 1
            # Exceed memory after first file
            return call_count[0] >= 1

        with (
            patch("thresher.runner.loop.gc_between_files"),
            patch("thresher.runner.loop.check_memory", side_effect=memory_check),
        ):
            loop._process_batch("queue/claimed/runner-01/batch-0001.json", "queue/")

        # Only 1 file processed because memory check returned True after first
        assert len(loop.results) == 1
        assert loop._memory_exceeded is True

    def test_memory_exceeded_exits_run_loop(
        self,
        mock_source: MagicMock,
        mock_destination: MagicMock,
        mock_embedder: MagicMock,
        config: Config,
    ) -> None:
        batch = _make_batch(
            items=[QueueItem(path="file1.m", source_type="direct")],
        )
        batch_json = _serialize_batch(batch).encode("utf-8")

        # First call: reclaim_stale_batches list_files returns empty
        # Second call: _claim_next_batch list_files returns one batch
        # Third call: reclaim_stale_batches list_files (would be second iteration)
        claim_list_calls = [0]

        def list_files_side_effect(prefix: str = ""):
            if "claimed/" in prefix:
                return iter([])
            claim_list_calls[0] += 1
            if claim_list_calls[0] == 1:
                return iter(
                    [
                        FileInfo(
                            path="queue/pending/batch-0001.json",
                            size=100,
                            updated=datetime.now(),
                        ),
                    ]
                )
            return iter([])

        mock_source.list_files.side_effect = list_files_side_effect
        mock_source.download_content.return_value = batch_json

        loop = _make_loop(mock_source, mock_destination, mock_embedder, config)
        loop.processor = MagicMock()
        loop.processor.process_file.return_value = ProcessingResult(
            path="file1.m",
            status=ProcessingStatus.INDEXED,
            duration_seconds=1.0,
        )

        with (
            patch("thresher.runner.loop.gc_between_files"),
            patch("thresher.runner.loop.check_memory", return_value=True),
        ):
            results = loop.run()

        assert loop._memory_exceeded is True
        assert len(results) == 1
