"""End-to-end functional tests: upload → classify → extract → chunk → embed → index.

Tests the full FileProcessor pipeline against real GCS (fake-gcs-server) and
real Qdrant, using only raw-text extraction (no docling/subprocess needed).
"""

from __future__ import annotations

import os

import pytest

from tests.functional.conftest import (
    FAKE_GCS_URL,
    GCS_BUCKET,
    QDRANT_URL,
    make_source_file,
    make_text_file,
)
from thresher.config import (
    Config,
    DestConfig,
    EmbeddingConfig,
    GCSConfig,
    ProcessingConfig,
    QdrantConfig,
    RoutingConfig,
    SourceConfig,
)
from thresher.types import (
    ChunkerConfig,
    EmbeddingModelConfig,
    FileTypeGroup,
    ProcessingStatus,
)

pytestmark = pytest.mark.functional

VECTOR_SIZE = 384
VECTOR_NAME = "fast-all-minilm-l6-v2"
COLLECTION = "functional-test"


def _make_config() -> Config:
    """Build a minimal Config for functional testing.

    Uses only raw-text extractor to avoid docling subprocess overhead.
    """
    return Config(
        source=SourceConfig(
            gcs=GCSConfig(
                bucket=GCS_BUCKET,
                source_prefix="source/",
                expanded_prefix="expanded/",
                cache_prefix="cache/",
                queue_prefix="queue/",
            ),
        ),
        destination=DestConfig(
            qdrant=QdrantConfig(
                url=QDRANT_URL,
                api_key="",
                timeout=30,
                batch_size=100,
            ),
        ),
        embedding=EmbeddingConfig(
            models={
                "default": EmbeddingModelConfig(
                    model="sentence-transformers/all-MiniLM-L6-v2",
                    vector_size=VECTOR_SIZE,
                    vector_name=VECTOR_NAME,
                    max_tokens=512,
                )
            }
        ),
        file_type_groups={
            "plain-text": FileTypeGroup(
                name="plain-text",
                extensions=[".txt", ".md", ".rst", ".log"],
                mime_types=["text/plain"],
                priority=90,
                extractor="raw-text",
                chunker=ChunkerConfig(strategy="chonkie-recursive", chunk_size=256),
            ),
            "general-source": FileTypeGroup(
                name="general-source",
                extensions=[".py", ".js", ".ts", ".java", ".go", ".rs"],
                mime_types=[],
                priority=60,
                extractor="raw-text",
                chunker=ChunkerConfig(strategy="chonkie-recursive", chunk_size=256),
            ),
        },
        routing=RoutingConfig(
            default_collection=COLLECTION,
            rules=[],
        ),
        processing=ProcessingConfig(
            per_file_timeout=60,
            docling_timeout=60,
        ),
        force=False,
    )


@pytest.fixture
def e2e_config():
    return _make_config()


@pytest.fixture
def source_provider(clean_bucket):
    """GCSSourceProvider wired to fake-gcs-server."""
    os.environ["STORAGE_EMULATOR_HOST"] = FAKE_GCS_URL
    from thresher.providers.gcs import GCSSourceProvider

    return GCSSourceProvider(
        bucket_name=GCS_BUCKET,
        source_prefix="source/",
        expanded_prefix="expanded/",
        cache_prefix="cache/",
        queue_prefix="queue/",
    )


@pytest.fixture
def dest_provider(clean_qdrant):
    """QdrantDestinationProvider wired to test Qdrant."""
    from thresher.providers.qdrant import QdrantDestinationProvider

    return QdrantDestinationProvider(
        url=QDRANT_URL,
        api_key="",
        timeout=30,
        batch_size=100,
        vector_name=VECTOR_NAME,
    )


@pytest.fixture(scope="session")
def embedder():
    from thresher.embedder import MultiModelEmbedder

    try:
        emb = MultiModelEmbedder(
            models={
                "default": EmbeddingModelConfig(
                    model="sentence-transformers/all-MiniLM-L6-v2",
                    vector_size=VECTOR_SIZE,
                    vector_name=VECTOR_NAME,
                    max_tokens=512,
                )
            }
        )
        emb.preload("default")
        return emb
    except Exception as exc:
        pytest.skip(f"Embedding model not available: {exc}")


@pytest.fixture
def router(e2e_config):
    from thresher.processing.router import Router

    return Router(
        rules=e2e_config.routing.rules,
        default_collection=e2e_config.routing.default_collection,
    )


@pytest.fixture
def processor(source_provider, dest_provider, embedder, router, e2e_config):
    from thresher.runner.processor import FileProcessor

    return FileProcessor(
        source=source_provider,
        destination=dest_provider,
        embedder=embedder,
        router=router,
        config=e2e_config,
    )


class TestTextFileEndToEnd:
    """Process a plain text file through the full pipeline."""

    def test_text_file_indexed(self, processor, source_provider, clean_qdrant):
        # Upload a text file
        content = make_text_file("Hello, Thresher!\nThis is a functional test.\n")
        source_provider.upload_content("source/hello.txt", content)

        # Process it
        result = processor.process_file("source/hello.txt")

        assert result.status == ProcessingStatus.INDEXED
        assert result.chunk_count is not None
        assert result.chunk_count > 0
        assert result.collection == COLLECTION
        assert result.content_hash is not None
        assert result.file_type_group == "plain-text"

        # Verify chunks exist in Qdrant
        points = clean_qdrant.scroll(COLLECTION, limit=100)[0]
        assert len(points) == result.chunk_count
        # Check metadata
        first = points[0]
        assert first.payload["source"] == "source/hello.txt"
        assert first.payload["content_hash"] == result.content_hash
        assert first.payload["metadata"]["collection"] == COLLECTION

    def test_large_text_produces_multiple_chunks(self, processor, source_provider, clean_qdrant):
        # Create content large enough for multiple chunks (~256 token chunks)
        lines = [f"Paragraph {i}: " + "word " * 80 for i in range(10)]
        content = make_text_file("\n\n".join(lines))
        source_provider.upload_content("source/big.txt", content)

        result = processor.process_file("source/big.txt")

        assert result.status == ProcessingStatus.INDEXED
        assert result.chunk_count is not None
        assert result.chunk_count > 1


class TestSourceCodeEndToEnd:
    """Process a source code file through the full pipeline."""

    def test_python_file_indexed(self, processor, source_provider, clean_qdrant):
        code = make_source_file(
            "def greet(name: str) -> str:\n"
            '    """Return a greeting."""\n'
            "    return f'Hello, {name}!'\n\n"
            "if __name__ == '__main__':\n"
            "    print(greet('World'))\n"
        )
        source_provider.upload_content("source/greet.py", code)

        result = processor.process_file("source/greet.py")

        assert result.status == ProcessingStatus.INDEXED
        assert result.chunk_count is not None
        assert result.chunk_count > 0
        assert result.file_type_group == "general-source"


class TestContentHashDedup:
    """Verify content-hash deduplication skips re-indexing unchanged files."""

    def test_same_content_skipped_on_second_run(self, processor, source_provider, clean_qdrant):
        content = make_text_file("Duplicate content test\n")
        source_provider.upload_content("source/dup.txt", content)

        # First run — should index
        r1 = processor.process_file("source/dup.txt")
        assert r1.status == ProcessingStatus.INDEXED

        # Second run — same content hash, should skip
        r2 = processor.process_file("source/dup.txt")
        assert r2.status == ProcessingStatus.SKIPPED

    def test_changed_content_reindexed(self, processor, source_provider, clean_qdrant):
        source_provider.upload_content("source/change.txt", make_text_file("Version 1\n"))
        r1 = processor.process_file("source/change.txt")
        assert r1.status == ProcessingStatus.INDEXED

        # Upload different content at same path
        source_provider.upload_content("source/change.txt", make_text_file("Version 2\n"))
        # Need to force or delete old entry — in real pipeline the controller handles this.
        # We process with force=True config variant:
        forced_config = _make_config()
        forced_config.force = True

        from thresher.runner.processor import FileProcessor

        forced_processor = FileProcessor(
            source=processor.source,
            destination=processor.destination,
            embedder=processor.embedder,
            router=processor.router,
            config=forced_config,
        )
        r2 = forced_processor.process_file("source/change.txt")
        assert r2.status == ProcessingStatus.INDEXED
        assert r2.content_hash != r1.content_hash


class TestUnknownFileSkipped:
    """Files with unknown extensions and non-text content should be skipped."""

    def test_unknown_binary_extension_skipped(self, processor, source_provider):
        # Binary content that won't match any text MIME type
        binary_blob = bytes(range(256)) * 4
        source_provider.upload_content("source/mystery.xyz123", binary_blob)
        result = processor.process_file("source/mystery.xyz123")
        assert result.status == ProcessingStatus.SKIPPED


class TestMultipleFilesEndToEnd:
    """Process multiple files in sequence, verifying all are indexed."""

    def test_multiple_files_all_indexed(self, processor, source_provider, clean_qdrant):
        files = {
            "source/a.txt": make_text_file("File A content\n"),
            "source/b.txt": make_text_file("File B content\n"),
            "source/c.py": make_source_file("x = 42\nprint(x)\n"),
        }
        for path, data in files.items():
            source_provider.upload_content(path, data)

        results = []
        for path in files:
            results.append(processor.process_file(path))

        indexed = [r for r in results if r.status == ProcessingStatus.INDEXED]
        assert len(indexed) == 3

        # Verify total points in Qdrant
        total_chunks = sum(r.chunk_count or 0 for r in indexed)
        points = clean_qdrant.scroll(COLLECTION, limit=1000)[0]
        assert len(points) == total_chunks
