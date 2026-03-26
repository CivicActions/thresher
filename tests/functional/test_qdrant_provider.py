"""Functional tests for QdrantDestinationProvider against real Qdrant."""

from __future__ import annotations

import pytest

from tests.functional.conftest import QDRANT_URL
from thresher.types import IndexChunk, make_point_id

pytestmark = pytest.mark.functional

VECTOR_SIZE = 384
VECTOR_NAME = "test-vector"


@pytest.fixture
def qdrant_provider(clean_qdrant):
    """Create a QdrantDestinationProvider pointing at the test Qdrant."""
    from thresher.providers.qdrant import QdrantDestinationProvider

    return QdrantDestinationProvider(
        url=QDRANT_URL,
        api_key="",
        timeout=30,
        batch_size=10,
        vector_name=VECTOR_NAME,
    )


def _make_chunk(
    source: str = "source/test.txt",
    text: str = "test chunk",
    content_hash: str = "abc123",
    chunk_index: int = 0,
) -> IndexChunk:
    return IndexChunk(
        point_id=make_point_id(source, chunk_index),
        text=text,
        vector=[0.1] * VECTOR_SIZE,
        payload={
            "source": source,
            "content_hash": content_hash,
            "chunk_index": chunk_index,
            "text": text,
        },
    )


class TestEnsureCollection:
    def test_creates_collection(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("test-col", VECTOR_SIZE, VECTOR_NAME)
        collections = [c.name for c in clean_qdrant.get_collections().collections]
        assert "test-col" in collections

    def test_idempotent(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("test-col", VECTOR_SIZE, VECTOR_NAME)
        qdrant_provider.ensure_collection("test-col", VECTOR_SIZE, VECTOR_NAME)
        collections = [c.name for c in clean_qdrant.get_collections().collections]
        assert collections.count("test-col") == 1


class TestIndexChunks:
    def test_index_single_chunk(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("idx-test", VECTOR_SIZE, VECTOR_NAME)
        chunk = _make_chunk()
        qdrant_provider.index_chunks("idx-test", [chunk])

        result = clean_qdrant.scroll("idx-test", limit=10)
        points = result[0]
        assert len(points) == 1
        assert points[0].payload["source"] == "source/test.txt"

    def test_index_multiple_chunks(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("idx-multi", VECTOR_SIZE, VECTOR_NAME)
        chunks = [_make_chunk(chunk_index=i) for i in range(5)]
        qdrant_provider.index_chunks("idx-multi", chunks)

        result = clean_qdrant.scroll("idx-multi", limit=100)
        assert len(result[0]) == 5

    def test_index_empty_list(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("idx-empty", VECTOR_SIZE, VECTOR_NAME)
        qdrant_provider.index_chunks("idx-empty", [])
        result = clean_qdrant.scroll("idx-empty", limit=10)
        assert len(result[0]) == 0


class TestExistsByHash:
    def test_exists_after_indexing(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("hash-test", VECTOR_SIZE, VECTOR_NAME)
        chunk = _make_chunk(source="source/file.txt", content_hash="deadbeef")
        qdrant_provider.index_chunks("hash-test", [chunk])

        assert qdrant_provider.exists_by_hash("hash-test", "source/file.txt", "deadbeef") is True

    def test_not_exists_different_hash(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("hash-test2", VECTOR_SIZE, VECTOR_NAME)
        chunk = _make_chunk(source="source/file.txt", content_hash="aaaa")
        qdrant_provider.index_chunks("hash-test2", [chunk])

        assert qdrant_provider.exists_by_hash("hash-test2", "source/file.txt", "bbbb") is False

    def test_not_exists_empty_collection(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("hash-empty", VECTOR_SIZE, VECTOR_NAME)
        assert qdrant_provider.exists_by_hash("hash-empty", "source/x.txt", "whatever") is False


class TestDeleteBySource:
    def test_deletes_matching_points(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("del-test", VECTOR_SIZE, VECTOR_NAME)
        chunks = [
            _make_chunk(source="source/a.txt"),
            _make_chunk(source="source/a.txt", chunk_index=1),
            _make_chunk(source="source/b.txt"),
        ]
        qdrant_provider.index_chunks("del-test", chunks)

        qdrant_provider.delete_by_source("del-test", "source/a.txt")

        result = clean_qdrant.scroll("del-test", limit=100)
        remaining = result[0]
        assert len(remaining) == 1
        assert remaining[0].payload["source"] == "source/b.txt"

    def test_delete_nonexistent_source_is_safe(self, qdrant_provider, clean_qdrant):
        qdrant_provider.ensure_collection("del-safe", VECTOR_SIZE, VECTOR_NAME)
        qdrant_provider.delete_by_source("del-safe", "source/no_such.txt")


class TestClose:
    def test_close_does_not_error(self, qdrant_provider):
        qdrant_provider.close()
