"""Qdrant destination provider implementation."""

from __future__ import annotations

import logging

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    TextIndexParams,
    TextIndexType,
    TokenizerType,
    VectorParams,
)

from thresher.types import IndexChunk

logger = logging.getLogger("thresher.providers.qdrant")


class QdrantDestinationProvider:
    """Qdrant vector store destination provider."""

    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: str = "",
        timeout: int = 60,
        batch_size: int = 100,
        vector_name: str = "fast-all-minilm-l6-v2",
    ):
        self._client = QdrantClient(
            url=url,
            api_key=api_key or None,
            timeout=timeout,
        )
        self.batch_size = batch_size
        self.vector_name = vector_name

    def ensure_collection(self, name: str, vector_size: int, vector_name: str) -> None:
        if self._client.collection_exists(name):
            return
        logger.info(
            "Creating collection: %s (vector_size=%d, vector_name=%s)",
            name,
            vector_size,
            vector_name,
        )
        self._client.create_collection(
            collection_name=name,
            vectors_config={
                vector_name: VectorParams(
                    size=vector_size,
                    distance=Distance.COSINE,
                ),
            },
        )
        # Index the 'source' payload field for fast filter-based deletes and lookups.
        try:
            self._client.create_payload_index(
                collection_name=name,
                field_name="source",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as e:
            logger.warning("Keyword index creation failed for '%s': %s", name, e)
        # Add a text index on 'source' for partial path matching via MatchText.
        try:
            self._client.create_payload_index(
                collection_name=name,
                field_name="source",
                field_schema=TextIndexParams(
                    type=TextIndexType.TEXT,
                    tokenizer=TokenizerType.WORD,
                    min_token_len=1,
                    lowercase=True,
                ),
            )
        except Exception as e:
            logger.warning("Text index creation failed for '%s': %s", name, e)

    def index_chunks(self, collection: str, chunks: list[IndexChunk]) -> None:
        if not chunks:
            return
        points = [
            PointStruct(
                id=chunk.point_id,
                vector={chunk.vector_name or self.vector_name: chunk.vector},
                payload=chunk.payload,
            )
            for chunk in chunks
        ]
        for i in range(0, len(points), self.batch_size):
            batch = points[i : i + self.batch_size]
            self._client.upsert(collection_name=collection, points=batch)

    def exists_by_hash(self, collection: str, source_path: str, content_hash: str) -> bool:
        results = self._client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="source", match=MatchValue(value=source_path)),
                    FieldCondition(key="content_hash", match=MatchValue(value=content_hash)),
                ]
            ),
            limit=1,
        )
        points, _ = results
        return len(points) > 0

    def delete_by_source(self, collection: str, source_path: str) -> None:
        self._client.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[
                    FieldCondition(key="source", match=MatchValue(value=source_path)),
                ]
            ),
        )

    def close(self) -> None:
        self._client.close()
