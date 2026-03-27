"""Unit tests for Phase 10 polish features: dry-run summary, file size thresholds, queue summary."""

from __future__ import annotations

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
from thresher.controller.queue_builder import queue_summary
from thresher.controller.scanner import scan_summary
from thresher.runner.processor import FileProcessor
from thresher.types import (
    ChunkerConfig,
    FileTypeGroup,
    ProcessingStatus,
)

# ---------------------------------------------------------------------------
# scan_summary tests (T058)
# ---------------------------------------------------------------------------


class TestScanSummary:
    """Tests for scan_summary."""

    def test_empty_items(self):
        result = scan_summary([])
        assert result == {
            "total_files": 0,
            "by_group": {},
            "by_source_type": {},
            "total_size_bytes": 0,
        }

    def test_single_item(self):
        items = [
            {
                "path": "data/routine.m",
                "source_type": "direct",
                "file_type_group": "mumps",
                "file_size": 1024,
            }
        ]
        result = scan_summary(items)
        assert result["total_files"] == 1
        assert result["by_group"] == {"mumps": 1}
        assert result["by_source_type"] == {"direct": 1}
        assert result["total_size_bytes"] == 1024

    def test_multiple_groups_and_types(self):
        items = [
            {
                "path": "data/routine.m",
                "source_type": "direct",
                "file_type_group": "mumps",
                "file_size": 1024,
            },
            {
                "path": "data/report.pdf",
                "source_type": "direct",
                "file_type_group": "documents",
                "file_size": 2048,
            },
            {
                "path": "expanded/inner.m",
                "source_type": "expanded",
                "file_type_group": "mumps",
                "file_size": None,
            },
        ]
        result = scan_summary(items)
        assert result["total_files"] == 3
        assert result["by_group"] == {"mumps": 2, "documents": 1}
        assert result["by_source_type"] == {"direct": 2, "expanded": 1}
        assert result["total_size_bytes"] == 3072

    def test_missing_fields_use_defaults(self):
        items = [{"path": "file.txt"}]
        result = scan_summary(items)
        assert result["by_group"] == {"unknown": 1}
        assert result["by_source_type"] == {"direct": 1}
        assert result["total_size_bytes"] == 0


# ---------------------------------------------------------------------------
# queue_summary tests (T060)
# ---------------------------------------------------------------------------


class TestQueueSummary:
    """Tests for queue_summary."""

    def test_empty(self):
        result = queue_summary([], [])
        assert result == {
            "total_files": 0,
            "batches_created": 0,
            "batch_ids": [],
        }

    def test_with_batches(self):
        batch_ids = ["batch-0001", "batch-0002"]
        items = [{"path": f"file{i}.m"} for i in range(5)]
        result = queue_summary(batch_ids, items)
        assert result["total_files"] == 5
        assert result["batches_created"] == 2
        assert result["batch_ids"] == ["batch-0001", "batch-0002"]


# ---------------------------------------------------------------------------
# File size threshold tests (T059)
# ---------------------------------------------------------------------------


@pytest.fixture
def file_type_groups():
    return {
        "mumps": FileTypeGroup(
            name="mumps",
            extensions=[".m"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="mumps-label-boundary", chunk_size=512),
            max_file_size=500,
        ),
        "source-code": FileTypeGroup(
            name="source-code",
            extensions=[".py", ".js"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="chonkie-code", chunk_size=512),
            max_file_size=500,
        ),
        "documents": FileTypeGroup(
            name="documents",
            extensions=[".pdf"],
            extractor="docling",
            chunker=ChunkerConfig(strategy="docling-hybrid", chunk_size=512),
            max_file_size=1000,
        ),
        "markdown": FileTypeGroup(
            name="markdown",
            extensions=[".md"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="chonkie-recursive", chunk_size=512),
            max_file_size=1000,
        ),
    }


@pytest.fixture
def config(file_type_groups):
    cfg = Config()
    cfg.source = SourceConfig(
        provider="gcs",
        gcs=GCSConfig(bucket="test-bucket", source_prefix="data/"),
    )
    cfg.destination = DestConfig(
        provider="qdrant",
        qdrant=QdrantConfig(url="http://localhost:6333"),
    )
    cfg.file_type_groups = file_type_groups
    cfg.routing = RoutingConfig(
        default_collection="default",
        rules=[],
    )
    cfg.embedding = EmbeddingConfig(vector_size=384, vector_name="test-vec")
    cfg.queue = QueueConfig(batch_size=1000)
    cfg.processing = ProcessingConfig()
    return cfg


@pytest.fixture
def mock_source():
    source = MagicMock()
    source.cache_path.return_value = "cache/test.md"
    source.exists.return_value = False
    return source


@pytest.fixture
def mock_destination():
    return MagicMock()


@pytest.fixture
def mock_embedder():
    embedder = MagicMock()
    embedder.embed_texts.return_value = [[0.1, 0.2, 0.3]]
    return embedder


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.route.return_value = "default"
    return router


class TestFileSizeThreshold:
    """Tests for file size threshold enforcement in process_file."""

    def test_source_file_over_group_limit_is_skipped(
        self, mock_source, mock_destination, mock_embedder, mock_router, config
    ):
        """Source files (mumps) exceeding group max_file_size should be SKIPPED."""
        # 501 bytes exceeds mumps group max_file_size of 500
        mock_source.download_content.return_value = b"x" * 501

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        result = processor.process_file("data/routine.m", "mumps")

        assert result.status == ProcessingStatus.SKIPPED
        assert result.file_type_group == "mumps"
        mock_destination.index_chunks.assert_not_called()

    def test_code_file_over_group_limit_is_skipped(
        self, mock_source, mock_destination, mock_embedder, mock_router, config
    ):
        """Code files (chonkie-code) exceeding group max_file_size should be SKIPPED."""
        mock_source.download_content.return_value = b"x" * 501

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        result = processor.process_file("data/app.py", "source-code")

        assert result.status == ProcessingStatus.SKIPPED
        assert result.file_type_group == "source-code"

    def test_document_over_group_limit_is_skipped(
        self, mock_source, mock_destination, mock_embedder, mock_router, config
    ):
        """Document files exceeding group max_file_size should be SKIPPED."""
        # 1001 bytes exceeds documents group max_file_size of 1000
        mock_source.download_content.return_value = b"x" * 1001

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        result = processor.process_file("data/report.pdf", "documents")

        assert result.status == ProcessingStatus.SKIPPED
        assert result.file_type_group == "documents"
        mock_destination.index_chunks.assert_not_called()

    def test_source_file_under_threshold_passes(
        self, mock_source, mock_destination, mock_embedder, mock_router, config
    ):
        """Source file under group max_file_size should proceed to processing."""
        # 500 bytes is exactly at the limit (not over), should pass
        mock_source.download_content.return_value = b"HELLO ; routine\n Q\n"

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        with (
            patch(
                "thresher.runner.processor.dispatch_chunker",
                return_value=[{"text": "chunk1"}],
            ),
            patch(
                "thresher.runner.processor.resolve_source_url",
                return_value="http://example.com/file.m",
            ),
            patch(
                "thresher.runner.processor._extract",
                return_value=("extracted text", None),
            ),
        ):
            result = processor.process_file("data/routine.m", "mumps")

        assert result.status == ProcessingStatus.INDEXED

    def test_document_under_threshold_passes(
        self, mock_source, mock_destination, mock_embedder, mock_router, config
    ):
        """Document file under group max_file_size should proceed to processing."""
        mock_source.download_content.return_value = b"x" * 999

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        with (
            patch(
                "thresher.runner.processor.dispatch_chunker",
                return_value=[{"text": "chunk1"}],
            ),
            patch(
                "thresher.runner.processor.resolve_source_url",
                return_value="http://example.com/file.pdf",
            ),
            patch(
                "thresher.runner.processor._extract",
                return_value=("extracted text", None),
            ),
        ):
            result = processor.process_file("data/report.pdf", "documents")

        assert result.status == ProcessingStatus.INDEXED

    def test_markdown_no_limit_when_zero(
        self, mock_source, mock_destination, mock_embedder, mock_router, config
    ):
        """Markdown with max_file_size=1000 allows files under that limit."""
        # 600 bytes: under markdown group max_file_size of 1000
        mock_source.download_content.return_value = b"x" * 600

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        with (
            patch(
                "thresher.runner.processor.dispatch_chunker",
                return_value=[{"text": "chunk1"}],
            ),
            patch(
                "thresher.runner.processor.resolve_source_url",
                return_value="http://example.com/file.md",
            ),
            patch(
                "thresher.runner.processor._extract",
                return_value=("extracted text", None),
            ),
        ):
            result = processor.process_file("data/readme.md", "markdown")

        assert result.status == ProcessingStatus.INDEXED


# ---------------------------------------------------------------------------
# Dry-run CLI integration test (T058 + T060)
# ---------------------------------------------------------------------------


class TestDryRunCLI:
    """Tests for enhanced dry-run CLI output."""

    def test_dry_run_prints_summary(self, monkeypatch, capsys):
        """Controller --dry-run should print detailed summary."""
        from thresher.cli import main

        def mock_scan_direct(source, config):
            return [
                {
                    "path": "data/routine.m",
                    "source_type": "direct",
                    "file_type_group": "mumps",
                    "file_size": 1024,
                },
                {
                    "path": "data/report.pdf",
                    "source_type": "direct",
                    "file_type_group": "documents",
                    "file_size": 2048,
                },
            ], []

        def mock_scan_expanded(source, config):
            return [
                {
                    "path": "expanded/inner.m",
                    "source_type": "expanded",
                    "file_type_group": "mumps",
                    "file_size": None,
                },
            ]

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_direct_files", mock_scan_direct)
        monkeypatch.setattr("thresher.controller.scanner.scan_expanded_files", mock_scan_expanded)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        result = main(["controller", "--dry-run"])
        assert result == 0

        captured = capsys.readouterr().out
        assert "Dry run summary:" in captured
        assert "Total files: 2" in captured
        assert "Total size:" in captured
        assert "mumps: 1" in captured
        assert "documents: 1" in captured
        assert "direct: 2" in captured

    def test_dry_run_empty_scan(self, monkeypatch, capsys):
        """Controller --dry-run with no files should show zero counts."""
        from thresher.cli import main

        def mock_scan_direct(source, config):
            return [], []

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_direct_files", mock_scan_direct)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        result = main(["controller", "--dry-run"])
        assert result == 0

        captured = capsys.readouterr().out
        assert "Total files: 0" in captured


# ---------------------------------------------------------------------------
# Controller summary test (T060)
# ---------------------------------------------------------------------------


class TestControllerSummary:
    """Tests for controller summary reporting."""

    def test_controller_prints_summary(self, monkeypatch, capsys):
        """Controller (non-dry-run) should print summary with file and batch counts."""
        from thresher.cli import main

        def mock_scan_direct(source, config):
            return [
                {
                    "path": "data/routine.m",
                    "source_type": "direct",
                    "file_type_group": "mumps",
                    "file_size": 1024,
                },
            ], []

        def mock_build_queue(items, source, **kwargs):
            return ["batch-0001"]

        def mock_create_source(config):
            return MagicMock()

        monkeypatch.setattr("thresher.controller.scanner.scan_direct_files", mock_scan_direct)
        monkeypatch.setattr("thresher.controller.queue_builder.build_queue", mock_build_queue)
        monkeypatch.setattr("thresher.runner.processor.create_source_provider", mock_create_source)

        result = main(["controller"])
        assert result == 0

        captured = capsys.readouterr().out
        assert "1 files scanned" in captured
        assert "1 batches created" in captured
