"""Unit tests for Qdrant destination provider (all Qdrant calls mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from qdrant_client.http.models import (
    Distance,
    PayloadSchemaType,
    TextIndexParams,
    TextIndexType,
    TokenizerType,
    VectorParams,
)

from thresher.providers.destination import DestinationProvider
from thresher.providers.qdrant import QdrantDestinationProvider
from thresher.types import IndexChunk


@pytest.fixture()
def mock_qdrant():
    """Patch QdrantClient and return (provider, mock_client)."""
    with patch("thresher.providers.qdrant.QdrantClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        provider = QdrantDestinationProvider(
            url="http://localhost:6333",
            batch_size=2,
            vector_name="test-vector",
        )
        yield provider, mock_client


class TestProtocolConformance:
    def test_implements_destination_provider(self, mock_qdrant):
        provider, _ = mock_qdrant
        assert isinstance(provider, DestinationProvider)


class TestEnsureCollection:
    def test_creates_when_not_exists(self, mock_qdrant):
        provider, mock_client = mock_qdrant
        mock_client.collection_exists.return_value = False

        provider.ensure_collection("docs", vector_size=384, vector_name="test-vector")

        mock_client.collection_exists.assert_called_once_with("docs")
        mock_client.create_collection.assert_called_once_with(
            collection_name="docs",
            vectors_config={
                "test-vector": VectorParams(
                    size=384,
                    distance=Distance.COSINE,
                ),
            },
        )
        mock_client.create_payload_index.assert_any_call(
            collection_name="docs",
            field_name="source",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        mock_client.create_payload_index.assert_any_call(
            collection_name="docs",
            field_name="source",
            field_schema=TextIndexParams(
                type=TextIndexType.TEXT,
                tokenizer=TokenizerType.WORD,
                min_token_len=1,
                lowercase=True,
            ),
        )
        assert mock_client.create_payload_index.call_count == 2

    def test_skips_when_exists(self, mock_qdrant):
        provider, mock_client = mock_qdrant
        mock_client.collection_exists.return_value = True

        provider.ensure_collection("docs", vector_size=384, vector_name="test-vector")

        mock_client.collection_exists.assert_called_once_with("docs")
        mock_client.create_collection.assert_not_called()


class TestIndexChunks:
    def _make_chunk(self, idx: int) -> IndexChunk:
        return IndexChunk(
            point_id=f"point-{idx}",
            text=f"chunk text {idx}",
            vector=[0.1 * idx, 0.2 * idx, 0.3 * idx],
            payload={
                "document": f"chunk text {idx}",
                "metadata": {"source": "file.txt", "chunk_index": idx},
                "source": "file.txt",
                "content_hash": "abc123",
            },
        )

    def test_upserts_points_with_named_vectors(self, mock_qdrant):
        provider, mock_client = mock_qdrant
        chunks = [self._make_chunk(0)]

        provider.index_chunks("docs", chunks)

        mock_client.upsert.assert_called_once()
        args = mock_client.upsert.call_args
        assert args.kwargs["collection_name"] == "docs"
        points = args.kwargs["points"]
        assert len(points) == 1
        assert points[0].id == "point-0"
        assert "test-vector" in points[0].vector

    def test_batches_correctly(self, mock_qdrant):
        provider, mock_client = mock_qdrant
        # batch_size=2, so 5 chunks should produce 3 batches (2, 2, 1)
        chunks = [self._make_chunk(i) for i in range(5)]

        provider.index_chunks("docs", chunks)

        assert mock_client.upsert.call_count == 3
        batch_sizes = [len(c.kwargs["points"]) for c in mock_client.upsert.call_args_list]
        assert batch_sizes == [2, 2, 1]

    def test_empty_chunks_no_upsert(self, mock_qdrant):
        provider, mock_client = mock_qdrant

        provider.index_chunks("docs", [])

        mock_client.upsert.assert_not_called()


class TestDeleteBySource:
    def test_calls_delete_with_filter(self, mock_qdrant):
        provider, mock_client = mock_qdrant

        provider.delete_by_source("docs", "file.txt")

        mock_client.delete.assert_called_once()
        delete_args = mock_client.delete.call_args
        assert delete_args.kwargs["collection_name"] == "docs"
        selector = delete_args.kwargs["points_selector"]
        assert len(selector.must) == 1
        assert selector.must[0].key == "source"
        assert selector.must[0].match.value == "file.txt"


class TestClose:
    def test_calls_client_close(self, mock_qdrant):
        provider, mock_client = mock_qdrant

        provider.close()

        mock_client.close.assert_called_once()
