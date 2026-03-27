"""Unit tests for controller modules: scanner and queue_builder."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from thresher.config import Config, GCSConfig, SourceConfig
from thresher.controller.queue_builder import (
    _serialize_batch,
    build_queue,
    deserialize_batch,
)
from thresher.controller.scanner import scan_direct_files, scan_expanded_files, scan_files
from thresher.types import (
    ChunkerConfig,
    FileInfo,
    FileTypeGroup,
    QueueBatch,
    QueueItem,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def file_type_groups():
    """Sample file type groups for classification."""
    return {
        "mumps": FileTypeGroup(
            name="mumps",
            extensions=[".m", ".mps"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="mumps-label-boundary"),
        ),
        "documents": FileTypeGroup(
            name="documents",
            extensions=[".pdf", ".docx"],
            extractor="docling",
            chunker=ChunkerConfig(strategy="docling-hybrid"),
        ),
    }


@pytest.fixture
def config(file_type_groups):
    """Minimal config for controller tests."""
    cfg = Config()
    cfg.source = SourceConfig(
        provider="gcs",
        gcs=GCSConfig(
            bucket="test-bucket",
            source_prefix="data/",
            queue_prefix="queue/",
        ),
    )
    cfg.file_type_groups = file_type_groups
    return cfg


@pytest.fixture
def mock_source():
    """Mock SourceProvider."""
    source = MagicMock()
    source.upload_content = MagicMock()
    return source


# ---------------------------------------------------------------------------
# Scanner tests
# ---------------------------------------------------------------------------


class TestScanFiles:
    """Tests for scan_files."""

    def test_scan_files_returns_matching_files(self, mock_source, config):
        """scan_files should return dicts for files matching known groups."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/routine.m", size=1024, updated=datetime.now()),
                FileInfo(path="data/report.pdf", size=2048, updated=datetime.now()),
                FileInfo(path="data/unknown.xyz", size=512, updated=datetime.now()),
            ]
        )

        items = scan_files(mock_source, config)

        assert len(items) == 2
        assert items[0]["path"] == "data/routine.m"
        assert items[0]["file_type_group"] == "mumps"
        assert items[0]["source_type"] == "direct"
        assert items[0]["file_size"] == 1024
        assert items[1]["path"] == "data/report.pdf"
        assert items[1]["file_type_group"] == "documents"

    def test_scan_files_skips_directories(self, mock_source, config):
        """scan_files should skip entries ending with /."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/subdir/", size=0, updated=datetime.now()),
                FileInfo(path="data/routine.m", size=1024, updated=datetime.now()),
            ]
        )

        items = scan_files(mock_source, config)

        assert len(items) == 1
        assert items[0]["path"] == "data/routine.m"

    def test_scan_files_empty_source(self, mock_source, config):
        """scan_files with no files should return empty list."""
        mock_source.list_files.return_value = iter([])

        items = scan_files(mock_source, config)

        assert items == []

    def test_scan_files_all_unrecognized(self, mock_source, config):
        """scan_files with only unrecognized files returns empty list."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/mystery.abc", size=100, updated=datetime.now()),
                FileInfo(path="data/mystery.def", size=200, updated=datetime.now()),
            ]
        )

        items = scan_files(mock_source, config)

        assert items == []




class TestScanDirectFiles:
    """Tests for scan_direct_files (archives returned separately)."""

    def test_returns_direct_files_and_archives(self, mock_source, config):
        """scan_direct_files should separate archives from direct files."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/routine.m", size=1024, updated=datetime.now()),
                FileInfo(path="data/archive.zip", size=5000, updated=datetime.now()),
                FileInfo(path="data/report.pdf", size=2048, updated=datetime.now()),
            ]
        )

        items, archives = scan_direct_files(mock_source, config)

        assert len(items) == 2
        assert items[0]["path"] == "data/routine.m"
        assert items[1]["path"] == "data/report.pdf"
        assert len(archives) == 1
        assert archives[0].path == "data/archive.zip"

    def test_no_archives(self, mock_source, config):
        """No archives should return empty archive list."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/routine.m", size=1024, updated=datetime.now()),
            ]
        )

        items, archives = scan_direct_files(mock_source, config)

        assert len(items) == 1
        assert len(archives) == 0

    def test_only_archives(self, mock_source, config):
        """Only archives should return empty items list."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/test.zip", size=1024, updated=datetime.now()),
                FileInfo(path="data/test.tar.gz", size=2048, updated=datetime.now()),
            ]
        )

        items, archives = scan_direct_files(mock_source, config)

        assert len(items) == 0
        assert len(archives) == 2

    def test_empty_source(self, mock_source, config):
        """Empty source returns empty items and empty archives."""
        mock_source.list_files.return_value = iter([])

        items, archives = scan_direct_files(mock_source, config)

        assert items == []
        assert archives == []


class TestScanExpandedFiles:
    """Tests for scan_expanded_files."""

    def test_scans_expanded_prefix(self, mock_source, config):
        """scan_expanded_files should classify files under expanded prefix."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="expanded/archive/readme.m", size=512, updated=datetime.now()),
                FileInfo(
                    path="expanded/archive/.expansion-record.json",
                    size=100,
                    updated=datetime.now(),
                ),
                FileInfo(path="expanded/archive/doc.pdf", size=1024, updated=datetime.now()),
            ]
        )

        items = scan_expanded_files(mock_source, config)

        assert len(items) == 2
        paths = [i["path"] for i in items]
        assert "expanded/archive/readme.m" in paths
        assert "expanded/archive/doc.pdf" in paths
        assert all(i["source_type"] == "expanded" for i in items)

    def test_skips_expansion_records(self, mock_source, config):
        """scan_expanded_files should skip .expansion-record.json files."""
        mock_source.list_files.return_value = iter(
            [
                FileInfo(
                    path="expanded/test/.expansion-record.json",
                    size=100,
                    updated=datetime.now(),
                ),
            ]
        )

        items = scan_expanded_files(mock_source, config)
        assert items == []

    def test_empty_expanded_prefix(self, mock_source, config):
        """No expanded files returns empty list."""
        mock_source.list_files.return_value = iter([])

        items = scan_expanded_files(mock_source, config)
        assert items == []


# ---------------------------------------------------------------------------
# Queue builder tests
# ---------------------------------------------------------------------------


class TestBuildQueue:
    """Tests for build_queue."""

    def test_build_queue_creates_batches(self, mock_source):
        """build_queue should create batch files on the source provider."""
        items = [
            {
                "path": f"file{i}.m",
                "source_type": "direct",
                "file_type_group": "mumps",
                "file_size": 100,
            }
            for i in range(5)
        ]

        batch_ids = build_queue(items, mock_source, queue_prefix="queue/", batch_size=3)

        assert len(batch_ids) == 2
        assert batch_ids == ["batch-0001", "batch-0002"]
        assert mock_source.upload_content.call_count == 2

        # Verify first batch path
        first_call = mock_source.upload_content.call_args_list[0]
        assert first_call[0][0] == "queue/pending/batch-0001.json"

    def test_build_queue_single_batch(self, mock_source):
        """build_queue with fewer items than batch_size creates one batch."""
        items = [
            {"path": "file.m", "source_type": "direct", "file_type_group": "mumps", "file_size": 50}
        ]

        batch_ids = build_queue(items, mock_source, batch_size=1000)

        assert len(batch_ids) == 1
        assert batch_ids == ["batch-0001"]

    def test_build_queue_empty_items(self, mock_source):
        """build_queue with no items returns empty list and writes nothing."""
        batch_ids = build_queue([], mock_source)

        assert batch_ids == []
        mock_source.upload_content.assert_not_called()

    def test_build_queue_batch_content_is_valid_json(self, mock_source):
        """build_queue should write valid JSON batch data."""
        items = [
            {
                "path": "file.m",
                "source_type": "direct",
                "file_type_group": "mumps",
                "file_size": 100,
            }
        ]

        build_queue(items, mock_source, batch_size=1000)

        written_data = mock_source.upload_content.call_args_list[0][0][1]
        parsed = json.loads(written_data.decode("utf-8"))
        assert parsed["batch_id"] == "batch-0001"
        assert parsed["item_count"] == 1
        assert len(parsed["items"]) == 1
        assert parsed["items"][0]["path"] == "file.m"


# ---------------------------------------------------------------------------
# Serialization round-trip tests
# ---------------------------------------------------------------------------


class TestBatchSerialization:
    """Tests for batch serialization/deserialization round-trip."""

    def test_round_trip_basic(self):
        """Serializing then deserializing a batch should produce identical data."""
        batch = QueueBatch(
            batch_id="batch-0001",
            created_at=1700000000.0,
            item_count=2,
            items=[
                QueueItem(
                    path="file1.m",
                    source_type="direct",
                    file_type_group="mumps",
                    file_size=100,
                ),
                QueueItem(
                    path="file2.pdf",
                    source_type="direct",
                    file_type_group="documents",
                    file_size=200,
                ),
            ],
        )

        serialized = _serialize_batch(batch)
        restored = deserialize_batch(serialized)

        assert restored.batch_id == batch.batch_id
        assert restored.created_at == batch.created_at
        assert restored.item_count == batch.item_count
        assert len(restored.items) == 2
        assert restored.items[0].path == "file1.m"
        assert restored.items[0].file_type_group == "mumps"
        assert restored.items[1].path == "file2.pdf"
        assert restored.items[1].file_size == 200

    def test_round_trip_with_claim_info(self):
        """Round-trip should preserve claimed_at and runner_id."""
        batch = QueueBatch(
            batch_id="batch-0002",
            created_at=1700000000.0,
            item_count=1,
            items=[
                QueueItem(path="file.m", source_type="direct"),
            ],
            claimed_at=1700001000.0,
            runner_id="runner-01",
        )

        serialized = _serialize_batch(batch)
        restored = deserialize_batch(serialized)

        assert restored.claimed_at == 1700001000.0
        assert restored.runner_id == "runner-01"

    def test_round_trip_with_item_status(self):
        """Round-trip should preserve item status and error info."""
        batch = QueueBatch(
            batch_id="batch-0003",
            created_at=1700000000.0,
            item_count=1,
            items=[
                QueueItem(
                    path="file.m",
                    source_type="direct",
                    status="failed",
                    attempt_count=2,
                    last_error="timeout",
                    completed_at=1700002000.0,
                ),
            ],
        )

        serialized = _serialize_batch(batch)
        restored = deserialize_batch(serialized)

        item = restored.items[0]
        assert item.status == "failed"
        assert item.attempt_count == 2
        assert item.last_error == "timeout"
        assert item.completed_at == 1700002000.0

    def test_deserialize_missing_optional_fields(self):
        """deserialize_batch should handle missing optional fields gracefully."""
        raw_json = json.dumps(
            {
                "batch_id": "batch-0001",
                "created_at": 1700000000.0,
                "item_count": 1,
                "items": [
                    {"path": "file.m", "source_type": "direct"},
                ],
            }
        )

        batch = deserialize_batch(raw_json)

        assert batch.batch_id == "batch-0001"
        assert batch.claimed_at is None
        assert batch.runner_id is None
        assert batch.items[0].status == "pending"
        assert batch.items[0].attempt_count == 0
        assert batch.items[0].file_type_group is None
