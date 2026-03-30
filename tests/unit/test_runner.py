"""Unit tests for runner modules: processor and loop."""

from __future__ import annotations

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
from thresher.runner.processor import (
    FileProcessor,
    create_destination_provider,
    create_source_provider,
    dispatch_chunker,
)
from thresher.types import (
    ChunkerConfig,
    FileInfo,
    FileTypeGroup,
    ProcessingStatus,
    QueueBatch,
    QueueItem,
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
            chunker=ChunkerConfig(
                strategy="mumps-label-boundary",
                chunk_size=512,
            ),
        ),
        "documents": FileTypeGroup(
            name="documents",
            extensions=[".pdf"],
            extractor="docling",
            chunker=ChunkerConfig(
                strategy="docling-hybrid",
                chunk_size=512,
            ),
        ),
        "markdown": FileTypeGroup(
            name="markdown",
            extensions=[".md"],
            extractor="raw-text",
            chunker=ChunkerConfig(
                strategy="chonkie-recursive",
                chunk_size=512,
            ),
        ),
        "source-code": FileTypeGroup(
            name="source-code",
            extensions=[".py", ".js"],
            extractor="raw-text",
            chunker=ChunkerConfig(
                strategy="chonkie-code",
                chunk_size=512,
            ),
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
    cfg.embedding = EmbeddingConfig(
        vector_size=384,
        vector_name="test-vec",
    )
    cfg.queue = QueueConfig(batch_size=1000)
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
    router.route.return_value = RouteResult(collection="default", embedding="default")
    return router


# ---------------------------------------------------------------------------
# FileProcessor tests
# ---------------------------------------------------------------------------


class TestFileProcessor:
    """Tests for FileProcessor.process_file."""

    def test_process_file_indexed(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        mock_router,
        config,
    ):
        """process_file returns INDEXED for a valid file."""
        mock_source.download_content.return_value = b"HELLO ; hello world routine\n Q\n"

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        with (
            patch(
                "thresher.runner.processor.classify_file",
                return_value="mumps",
            ),
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
            result = processor.process_file(
                "data/routine.m",
                "mumps",
            )

        assert result.status == ProcessingStatus.INDEXED
        assert result.path == "data/routine.m"
        assert result.chunk_count == 1
        assert result.collection == "default"
        mock_destination.index_chunks.assert_called_once()

    def test_process_file_skipped_no_group(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        mock_router,
        config,
    ):
        """process_file returns SKIPPED when classify returns None."""
        mock_source.download_content.return_value = b"data"

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        with patch(
            "thresher.runner.processor.classify_file",
            return_value=None,
        ):
            result = processor.process_file("data/unknown.xyz")

        assert result.status == ProcessingStatus.SKIPPED

    def test_process_file_failed_on_exception(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        mock_router,
        config,
    ):
        """process_file returns FAILED on exception."""
        mock_source.download_content.side_effect = RuntimeError(
            "download failed",
        )

        processor = FileProcessor(
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            router=mock_router,
            config=config,
        )

        result = processor.process_file("data/broken.m", "mumps")

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert "download failed" in result.error_message


# ---------------------------------------------------------------------------
# dispatch_chunker tests
# ---------------------------------------------------------------------------


class TestDispatchChunker:
    """Tests for dispatch_chunker selecting correct chunker."""

    def test_mumps_strategy(self):
        """mumps-label-boundary dispatches to chunk_mumps_source."""
        group = FileTypeGroup(
            name="mumps",
            chunker=ChunkerConfig(
                strategy="mumps-label-boundary",
                chunk_size=512,
            ),
        )

        with patch(
            "thresher.processing.chunkers.mumps_label.chunk_mumps_source",
            return_value=[{"text": "chunk"}],
        ) as mock_chunker:
            result = dispatch_chunker(
                "HELLO ; test\n Q\n",
                group,
            )

        assert len(result) == 1
        mock_chunker.assert_called_once()

    def test_recursive_strategy(self):
        """chonkie-recursive dispatches to chunk_with_recursive."""
        group = FileTypeGroup(
            name="markdown",
            chunker=ChunkerConfig(
                strategy="chonkie-recursive",
                chunk_size=512,
            ),
        )

        with patch(
            "thresher.processing.chunkers.chonkie_recursive.chunk_with_recursive",
            return_value=[{"text": "chunk"}],
        ) as mock_chunker:
            result = dispatch_chunker(
                "# Hello World\n\nSome text.",
                group,
            )

        assert len(result) == 1
        mock_chunker.assert_called_once()

    def test_chonkie_code_falls_back_to_recursive(self):
        """chonkie-code dispatches to chunk_code."""
        group = FileTypeGroup(
            name="source-code",
            chunker=ChunkerConfig(
                strategy="chonkie-code",
                chunk_size=512,
            ),
        )

        with patch(
            "thresher.processing.chunkers.chonkie_code.chunk_code",
            return_value=[{"text": "chunk"}],
        ) as mock_chunker:
            result = dispatch_chunker("def foo(): pass", group)

        assert len(result) == 1
        mock_chunker.assert_called_once()

    def test_docling_hybrid_with_json(self):
        """docling-hybrid with doc_json uses docling_hybrid chunker."""
        group = FileTypeGroup(
            name="documents",
            chunker=ChunkerConfig(
                strategy="docling-hybrid",
                chunk_size=512,
            ),
        )
        doc_json = '{"body": []}'

        with patch(
            "thresher.processing.chunkers.docling_hybrid.chunk_with_docling_hybrid",
            return_value=[{"text": "chunk", "headings": []}],
        ) as mock_chunker:
            result = dispatch_chunker(
                "Some text",
                group,
                doc_json=doc_json,
            )

        assert len(result) == 1
        mock_chunker.assert_called_once_with(
            doc_json,
            chunk_size=512,
        )

    def test_docling_hybrid_without_json_falls_back(self):
        """docling-hybrid without doc_json falls back to recursive."""
        group = FileTypeGroup(
            name="documents",
            chunker=ChunkerConfig(
                strategy="docling-hybrid",
                chunk_size=512,
            ),
        )

        with patch(
            "thresher.processing.chunkers.chonkie_recursive.chunk_with_recursive",
            return_value=[{"text": "chunk"}],
        ) as mock_chunker:
            result = dispatch_chunker(
                "Some text",
                group,
                doc_json=None,
            )

        assert len(result) == 1
        mock_chunker.assert_called_once()


# ---------------------------------------------------------------------------
# Provider factory tests
# ---------------------------------------------------------------------------


class TestProviderFactories:
    """Tests for create_source/destination_provider."""

    def test_create_source_provider_gcs(self, config):
        """create_source_provider with 'gcs' creates GCSSourceProvider."""
        with patch(
            "thresher.providers.gcs.GCSSourceProvider",
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            create_source_provider(config)

        mock_cls.assert_called_once_with(
            bucket_name="test-bucket",
            source_prefix="data/",
            expanded_prefix="expanded/",
            cache_prefix="cache/",
            queue_prefix="queue/",
        )

    def test_create_source_provider_unknown_raises(self, config):
        """create_source_provider with unknown provider raises."""
        config.source.provider = "s3"

        with pytest.raises(
            ValueError,
            match="Unknown source provider: s3",
        ):
            create_source_provider(config)

    def test_create_destination_provider_qdrant(self, config):
        """create_destination_provider creates QdrantDestProvider."""
        with patch(
            "thresher.providers.qdrant.QdrantDestinationProvider",
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            create_destination_provider(config)

        mock_cls.assert_called_once_with(
            url="http://localhost:6333",
            api_key="",
            timeout=60,
            batch_size=100,
            vector_name="test-vec",
        )

    def test_create_destination_provider_unknown_raises(self, config):
        """create_destination_provider with unknown provider raises."""
        config.destination.provider = "pinecone"

        with pytest.raises(
            ValueError,
            match="Unknown destination provider: pinecone",
        ):
            create_destination_provider(config)


# ---------------------------------------------------------------------------
# RunnerLoop tests
# ---------------------------------------------------------------------------


class TestRunnerLoop:
    """Tests for RunnerLoop claim logic."""

    def test_claim_next_batch_success(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        config,
    ):
        """_claim_next_batch claims a batch and returns path."""
        from thresher.controller.queue_builder import _serialize_batch
        from thresher.runner.loop import RunnerLoop

        batch = QueueBatch(
            batch_id="batch-0001",
            created_at=1700000000.0,
            item_count=1,
            items=[
                QueueItem(path="file.m", source_type="direct"),
            ],
        )
        batch_json = _serialize_batch(batch).encode("utf-8")

        mock_source.list_files.return_value = iter(
            [
                FileInfo(
                    path="queue/pending/batch-0001.json",
                    size=100,
                    updated=datetime.now(),
                ),
            ]
        )
        mock_source.download_content.return_value = batch_json

        loop = RunnerLoop(
            runner_id="runner-01",
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            config=config,
        )

        claim_path = loop._claim_next_batch("queue/")

        expected = "queue/claimed/runner-01/batch-0001.json"
        assert claim_path == expected
        mock_source.upload_content.assert_called_once()
        mock_source.delete.assert_called_once_with(
            "queue/pending/batch-0001.json",
        )

    def test_claim_next_batch_contention(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        config,
    ):
        """_claim_next_batch skips batches claimed by others."""
        from thresher.controller.queue_builder import _serialize_batch
        from thresher.runner.loop import RunnerLoop

        batch1 = QueueBatch(
            batch_id="batch-0001",
            created_at=1700000000.0,
            item_count=1,
            items=[
                QueueItem(path="file1.m", source_type="direct"),
            ],
        )
        batch2 = QueueBatch(
            batch_id="batch-0002",
            created_at=1700000000.0,
            item_count=1,
            items=[
                QueueItem(path="file2.m", source_type="direct"),
            ],
        )

        mock_source.list_files.return_value = iter(
            [
                FileInfo(
                    path="queue/pending/batch-0001.json",
                    size=100,
                    updated=datetime.now(),
                ),
                FileInfo(
                    path="queue/pending/batch-0002.json",
                    size=100,
                    updated=datetime.now(),
                ),
            ]
        )

        # First claim fails (contention), second succeeds
        call_count = [0]

        def side_effect(path, data, if_generation_match=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FileExistsError("already claimed")

        mock_source.upload_content.side_effect = side_effect
        mock_source.download_content.side_effect = [
            _serialize_batch(batch1).encode("utf-8"),
            _serialize_batch(batch2).encode("utf-8"),
        ]

        loop = RunnerLoop(
            runner_id="runner-01",
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            config=config,
        )

        with patch("thresher.runner.loop.random.shuffle"):
            claim_path = loop._claim_next_batch("queue/")

        expected = "queue/claimed/runner-01/batch-0002.json"
        assert claim_path == expected

    def test_claim_next_batch_no_pending(
        self,
        mock_source,
        mock_destination,
        mock_embedder,
        config,
    ):
        """_claim_next_batch returns None when no pending batches."""
        from thresher.runner.loop import RunnerLoop

        mock_source.list_files.return_value = iter([])

        loop = RunnerLoop(
            runner_id="runner-01",
            source=mock_source,
            destination=mock_destination,
            embedder=mock_embedder,
            config=config,
        )

        claim_path = loop._claim_next_batch("queue/")

        assert claim_path is None
