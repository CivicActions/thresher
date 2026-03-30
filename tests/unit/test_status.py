"""Tests for thresher.controller.status module."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from thresher.controller.status import (
    CollectionStatus,
    PipelineStatus,
    QueueStatus,
    format_status,
    get_collection_statuses,
    get_queue_status,
)
from thresher.types import FileInfo


def _make_fi(path: str, ts: datetime | None = None) -> FileInfo:
    """Create a FileInfo with sensible defaults."""
    return FileInfo(
        path=path,
        size=1024,
        updated=ts or datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


class TestGetQueueStatus:
    """Tests for get_queue_status."""

    def test_empty_queue(self) -> None:
        source = MagicMock()
        source.list_files.return_value = iter([])
        source.exists.return_value = False

        result = get_queue_status(source, "queue/")

        assert result.pending == 0
        assert result.claimed == 0
        assert result.done == 0
        assert result.total == 0

    def test_counts_batches_by_state(self) -> None:
        pending = [
            _make_fi("queue/pending/batch-001.json"),
            _make_fi("queue/pending/batch-002.json"),
        ]
        done = [
            _make_fi(
                "queue/done/batch-003.json",
                datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
        ]
        retry: list[FileInfo] = []

        def fake_list(prefix: str, recursive: bool = True):
            if "pending/" in prefix:
                return iter(pending)
            elif "done/" in prefix:
                return iter(done)
            elif "retry/" in prefix:
                return iter(retry)
            elif "claimed/" in prefix:
                return iter([_make_fi("queue/claimed/runner-1/batch-004.json")])
            return iter([])

        source = MagicMock()
        source.list_files.side_effect = fake_list
        source.exists.return_value = False

        result = get_queue_status(source, "queue/")

        assert result.pending == 2
        assert result.done == 1
        assert result.claimed == 1
        assert result.total == 4

    def test_ignores_non_json_files(self) -> None:
        source = MagicMock()
        source.list_files.side_effect = lambda prefix, **kw: iter([_make_fi(f"{prefix}readme.txt")])
        source.exists.return_value = False

        result = get_queue_status(source, "queue/")
        assert result.total == 0

    def test_reads_skip_list(self) -> None:
        import json

        source = MagicMock()
        source.list_files.return_value = iter([])
        source.exists.return_value = True
        source.download_content.return_value = json.dumps(["a.txt", "b.txt"]).encode()

        result = get_queue_status(source, "queue/")
        assert result.skip_list_size == 2

    def test_done_timestamps_tracked(self) -> None:
        early = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        late = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        done = [_make_fi("queue/done/b1.json", early), _make_fi("queue/done/b2.json", late)]

        source = MagicMock()
        source.list_files.side_effect = lambda prefix, **kw: (
            iter(done) if "done/" in prefix else iter([])
        )
        source.exists.return_value = False

        result = get_queue_status(source, "queue/")
        assert result.oldest_done_ts == early.timestamp()
        assert result.newest_done_ts == late.timestamp()


class TestGetCollectionStatuses:
    """Tests for get_collection_statuses."""

    def test_returns_collection_info(self) -> None:
        mock_config = MagicMock()
        mock_config.destination.qdrant.url = "http://localhost:6333"
        mock_config.destination.qdrant.api_key = None
        mock_config.destination.qdrant.timeout = 30

        mock_col = MagicMock()
        mock_col.name = "test-col"

        mock_info = MagicMock()
        mock_info.points_count = 42
        mock_info.status = "green"

        with patch("qdrant_client.QdrantClient") as MockClient:
            client_instance = MockClient.return_value
            client_instance.get_collections.return_value.collections = [mock_col]
            client_instance.get_collection.return_value = mock_info

            result = get_collection_statuses(mock_config)

        assert len(result) == 1
        assert result[0].name == "test-col"
        assert result[0].points_count == 42

    def test_handles_connection_error(self) -> None:
        mock_config = MagicMock()
        mock_config.destination.qdrant.url = "http://bad:6333"
        mock_config.destination.qdrant.api_key = None
        mock_config.destination.qdrant.timeout = 5

        with patch(
            "qdrant_client.QdrantClient",
            side_effect=Exception("connection refused"),
        ):
            result = get_collection_statuses(mock_config)

        assert result == []


class TestFormatStatus:
    """Tests for format_status."""

    def test_basic_output(self) -> None:
        status = PipelineStatus(
            queue=QueueStatus(pending=10, claimed=5, done=85, total=100),
            collections=[CollectionStatus(name="my-col", points_count=1000, status="green")],
            batch_size=250,
        )
        output = format_status(status)

        assert "Done:    85 batches (85%)" in output
        assert "Claimed: 5 batches" in output
        assert "Pending: 10 batches" in output
        assert "Total:   100 batches" in output
        assert "my-col: 1,000 points" in output
        assert "Total: 1,000 points" in output

    def test_shows_retry_when_present(self) -> None:
        status = PipelineStatus(
            queue=QueueStatus(pending=5, retry=3, total=8),
        )
        output = format_status(status)
        assert "Retry:   3 batches" in output

    def test_hides_retry_when_zero(self) -> None:
        status = PipelineStatus(queue=QueueStatus(pending=5, total=5))
        output = format_status(status)
        assert "Retry" not in output

    def test_eta_calculation(self) -> None:
        early = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc).timestamp()
        late = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()
        status = PipelineStatus(
            queue=QueueStatus(
                pending=100,
                done=200,
                total=300,
                oldest_done_ts=early,
                newest_done_ts=late,
            ),
        )
        output = format_status(status)
        assert "batches/hr" in output
        assert "ETA" in output

    def test_zero_total_no_division_error(self) -> None:
        status = PipelineStatus(queue=QueueStatus())
        output = format_status(status)
        assert "0%" in output
