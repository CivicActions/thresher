"""Unit tests for US5 — skip list, content-hash dedup, and --force flag."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from thresher.config import (
    Config,
    DestConfig,
    EmbeddingConfig,
    GCSConfig,
    QdrantConfig,
    QueueConfig,
    RoutingConfig,
    SourceConfig,
)
from thresher.controller.scanner import (
    _load_skip_list,
    _save_skip_list,
    scan_files,
    update_skip_list,
)
from thresher.types import (
    ChunkerConfig,
    FileInfo,
    FileTypeGroup,
    ProcessingStatus,
    RouteResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def file_type_groups():
    return {
        "mumps": FileTypeGroup(
            name="mumps",
            extensions=[".m"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="mumps-label-boundary"),
        ),
        "documents": FileTypeGroup(
            name="documents",
            extensions=[".pdf"],
            extractor="docling",
            chunker=ChunkerConfig(strategy="docling-hybrid"),
        ),
    }


@pytest.fixture
def config(file_type_groups):
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
    source = MagicMock()
    source.upload_content = MagicMock()
    source.exists.return_value = False
    return source


@pytest.fixture
def mock_destination():
    dest = MagicMock()
    dest.exists_by_hash.return_value = False
    return dest


@pytest.fixture
def mock_embedder():
    embedder = MagicMock()
    embedder.embed_texts.return_value = [[0.1, 0.2, 0.3]]
    return embedder


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.route.return_value = RouteResult(collection="default", embedding="default")
    return router


@pytest.fixture
def processor_config(file_type_groups):
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
    return cfg


# ---------------------------------------------------------------------------
# _load_skip_list tests
# ---------------------------------------------------------------------------


class TestLoadSkipList:
    """Tests for _load_skip_list."""

    def test_returns_empty_set_when_file_missing(self, mock_source):
        """Should return empty set when skip-list.json does not exist."""
        mock_source.exists.return_value = False

        result = _load_skip_list(mock_source, "queue/")

        assert result == set()
        mock_source.exists.assert_called_once_with("queue/skip-list.json")

    def test_loads_existing_skip_list(self, mock_source):
        """Should return set of paths from existing skip-list.json."""
        mock_source.exists.return_value = True
        skip_data = json.dumps(["data/a.m", "data/b.pdf"]).encode("utf-8")
        mock_source.download_content.return_value = skip_data

        result = _load_skip_list(mock_source, "queue/")

        assert result == {"data/a.m", "data/b.pdf"}

    def test_handles_corrupt_json_gracefully(self, mock_source):
        """Should return empty set and not crash on corrupt JSON."""
        mock_source.exists.return_value = True
        mock_source.download_content.return_value = b"not valid json{{"

        result = _load_skip_list(mock_source, "queue/")

        assert result == set()

    def test_handles_download_error_gracefully(self, mock_source):
        """Should return empty set when download fails."""
        mock_source.exists.return_value = True
        mock_source.download_content.side_effect = RuntimeError("network error")

        result = _load_skip_list(mock_source, "queue/")

        assert result == set()


# ---------------------------------------------------------------------------
# _save_skip_list / update_skip_list tests
# ---------------------------------------------------------------------------


class TestSaveSkipList:
    """Tests for _save_skip_list."""

    def test_saves_sorted_json(self, mock_source):
        """Should write sorted JSON array to source provider."""
        _save_skip_list(mock_source, "queue/", {"data/b.m", "data/a.m"})

        mock_source.upload_content.assert_called_once()
        path, data = mock_source.upload_content.call_args[0]
        assert path == "queue/skip-list.json"
        parsed = json.loads(data.decode("utf-8"))
        assert parsed == ["data/a.m", "data/b.m"]


class TestUpdateSkipList:
    """Tests for update_skip_list."""

    def test_adds_paths_to_empty_list(self, mock_source):
        """Should create a skip list when none exists."""
        mock_source.exists.return_value = False

        update_skip_list(mock_source, "queue/", ["data/new.m"])

        path, data = mock_source.upload_content.call_args[0]
        assert path == "queue/skip-list.json"
        parsed = json.loads(data.decode("utf-8"))
        assert parsed == ["data/new.m"]

    def test_merges_with_existing_list(self, mock_source):
        """Should merge new paths with existing skip list."""
        mock_source.exists.return_value = True
        existing = json.dumps(["data/old.m"]).encode("utf-8")
        mock_source.download_content.return_value = existing

        update_skip_list(mock_source, "queue/", ["data/new.m"])

        path, data = mock_source.upload_content.call_args[0]
        parsed = json.loads(data.decode("utf-8"))
        assert set(parsed) == {"data/old.m", "data/new.m"}

    def test_deduplicates_paths(self, mock_source):
        """Should not duplicate paths already in the skip list."""
        mock_source.exists.return_value = True
        existing = json.dumps(["data/a.m"]).encode("utf-8")
        mock_source.download_content.return_value = existing

        update_skip_list(mock_source, "queue/", ["data/a.m", "data/b.m"])

        path, data = mock_source.upload_content.call_args[0]
        parsed = json.loads(data.decode("utf-8"))
        assert parsed == ["data/a.m", "data/b.m"]


# ---------------------------------------------------------------------------
# scan_files skip list integration tests
# ---------------------------------------------------------------------------


class TestScanFilesSkipList:
    """Tests for scan_files with skip list filtering."""

    def test_skip_list_filters_files(self, mock_source, config):
        """scan_files should exclude files present in the skip list."""
        mock_source.exists.return_value = True
        skip_data = json.dumps(["data/already.m"]).encode("utf-8")
        mock_source.download_content.return_value = skip_data

        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/already.m", size=100, updated=datetime.now()),
                FileInfo(path="data/new.m", size=200, updated=datetime.now()),
            ]
        )

        items = scan_files(mock_source, config)

        assert len(items) == 1
        assert items[0]["path"] == "data/new.m"

    def test_force_ignores_skip_list(self, mock_source, config):
        """scan_files with force=True should not filter by skip list."""
        config.force = True

        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/already.m", size=100, updated=datetime.now()),
                FileInfo(path="data/new.m", size=200, updated=datetime.now()),
            ]
        )

        items = scan_files(mock_source, config)

        assert len(items) == 2
        # Should not even check for skip list
        mock_source.exists.assert_not_called()

    def test_empty_skip_list_no_filtering(self, mock_source, config):
        """scan_files with empty skip list should return all classifiable files."""
        mock_source.exists.return_value = False

        mock_source.list_files.return_value = iter(
            [
                FileInfo(path="data/a.m", size=100, updated=datetime.now()),
                FileInfo(path="data/b.pdf", size=200, updated=datetime.now()),
            ]
        )

        items = scan_files(mock_source, config)

        assert len(items) == 2


# ---------------------------------------------------------------------------
# Content-hash dedup tests
# ---------------------------------------------------------------------------


class TestContentHashDedup:
    """Tests for content-hash dedup in FileProcessor."""

    def test_dedup_skips_when_hash_exists(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        mock_router,
        processor_config,
    ):
        """process_file returns SKIPPED when content hash already exists."""
        from thresher.runner.processor import FileProcessor

        mock_source.download_content.return_value = b"HELLO ; routine\n Q\n"
        mock_source.cache_path.return_value = "cache/test.md"
        mock_destination.exists_by_hash.return_value = True

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=processor_config,
        )

        with (
            patch(
                "thresher.runner.processor.classify_file",
                return_value="mumps",
            ),
            patch(
                "thresher.runner.processor._extract",
                return_value=("extracted text", None),
            ),
        ):
            result = processor.process_file("data/routine.m", "mumps")

        assert result.status == ProcessingStatus.SKIPPED
        assert result.content_hash is not None
        # Should NOT have called embed or index
        mock_embedder.embed_texts.assert_not_called()
        mock_destination.index_chunks.assert_not_called()

    def test_dedup_skipped_with_force(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        mock_router,
        processor_config,
    ):
        """process_file with force=True bypasses content-hash dedup."""
        from thresher.runner.processor import FileProcessor

        processor_config.force = True
        mock_source.download_content.return_value = b"HELLO ; routine\n Q\n"
        mock_source.cache_path.return_value = "cache/test.md"
        mock_destination.exists_by_hash.return_value = True

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=processor_config,
        )

        with (
            patch(
                "thresher.runner.processor.classify_file",
                return_value="mumps",
            ),
            patch(
                "thresher.runner.processor._extract",
                return_value=("extracted text", None),
            ),
            patch(
                "thresher.runner.processor.dispatch_chunker",
                return_value=[{"text": "chunk1"}],
            ),
            patch(
                "thresher.runner.processor.resolve_source_url",
                return_value="http://example.com/file.m",
            ),
        ):
            result = processor.process_file("data/routine.m", "mumps")

        assert result.status == ProcessingStatus.INDEXED
        # exists_by_hash should NOT even be called when force is True
        mock_destination.exists_by_hash.assert_not_called()
        mock_destination.index_chunks.assert_called_once()

    def test_dedup_allows_new_content(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        mock_router,
        processor_config,
    ):
        """process_file indexes when content hash is new (not in destination)."""
        from thresher.runner.processor import FileProcessor

        mock_source.download_content.return_value = b"HELLO ; routine\n Q\n"
        mock_source.cache_path.return_value = "cache/test.md"
        mock_destination.exists_by_hash.return_value = False

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=processor_config,
        )

        with (
            patch(
                "thresher.runner.processor.classify_file",
                return_value="mumps",
            ),
            patch(
                "thresher.runner.processor._extract",
                return_value=("extracted text", None),
            ),
            patch(
                "thresher.runner.processor.dispatch_chunker",
                return_value=[{"text": "chunk1"}],
            ),
            patch(
                "thresher.runner.processor.resolve_source_url",
                return_value="http://example.com/file.m",
            ),
        ):
            result = processor.process_file("data/routine.m", "mumps")

        assert result.status == ProcessingStatus.INDEXED
        mock_destination.index_chunks.assert_called_once()


# ---------------------------------------------------------------------------
# --force flag CLI propagation tests
# ---------------------------------------------------------------------------


class TestForceFlag:
    """Tests for --force flag propagation in CLI."""

    def test_controller_force_flag(self):
        """controller --force sets config.force to True."""
        from thresher.cli import main

        with (
            patch("thresher.cli.load_config") as mock_load,
            patch("thresher.cli._run_controller") as mock_run,
        ):
            cfg = Config()
            mock_load.return_value = cfg
            mock_run.return_value = 0

            main(["--config", "test.yaml", "controller", "--force"])

            args = mock_run.call_args[0]
            config_arg, args_arg = args
            assert args_arg.force is True

    def test_controller_no_force_flag(self):
        """controller without --force keeps config.force as False."""
        from thresher.cli import main

        with (
            patch("thresher.cli.load_config") as mock_load,
            patch("thresher.cli._run_controller") as mock_run,
        ):
            cfg = Config()
            mock_load.return_value = cfg
            mock_run.return_value = 0

            main(["--config", "test.yaml", "controller", "--dry-run"])

            args = mock_run.call_args[0]
            config_arg, args_arg = args
            assert args_arg.force is False

    def test_runner_force_flag(self):
        """runner --force sets config.force to True."""
        from thresher.cli import main

        with (
            patch("thresher.cli.load_config") as mock_load,
            patch("thresher.cli._run_runner") as mock_run,
        ):
            cfg = Config()
            mock_load.return_value = cfg
            mock_run.return_value = 0

            main(["--config", "test.yaml", "runner", "--runner-id", "r1", "--force"])

            args = mock_run.call_args[0]
            config_arg, args_arg = args
            assert args_arg.force is True

    def test_runner_no_force_flag(self):
        """runner without --force keeps default."""
        from thresher.cli import main

        with (
            patch("thresher.cli.load_config") as mock_load,
            patch("thresher.cli._run_runner") as mock_run,
        ):
            cfg = Config()
            mock_load.return_value = cfg
            mock_run.return_value = 0

            main(["--config", "test.yaml", "runner", "--runner-id", "r1"])

            args = mock_run.call_args[0]
            config_arg, args_arg = args
            assert args_arg.force is False
