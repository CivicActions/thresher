"""Qdrant destination provider implementation."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

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

# Retry config for transient Qdrant errors (timeouts, connection resets)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds; doubles each retry


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

    def _retry(self, operation: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call *fn* with retry and exponential backoff on transient errors."""
        for attempt in range(_MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Qdrant %s failed (attempt %d/%d), retrying in %.1fs: %s",
                    operation,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    def ensure_collection(self, name: str, vector_size: int, vector_name: str) -> None:
        if self._retry("collection_exists", self._client.collection_exists, name):
            return
        logger.info(
            "Creating collection: %s (vector_size=%d, vector_name=%s)",
            name,
            vector_size,
            vector_name,
        )
        self._retry(
            "create_collection",
            self._client.create_collection,
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
            self._retry(
                "create_payload_index",
                self._client.create_payload_index,
                collection_name=name,
                field_name="source",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as e:
            logger.warning("Keyword index creation failed for '%s': %s", name, e)
        # Add a text index on 'source' for partial path matching via MatchText.
        try:
            self._retry(
                "create_payload_index",
                self._client.create_payload_index,
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
            self._retry(
                "upsert",
                self._client.upsert,
                collection_name=collection,
                points=batch,
                wait=False,
            )

    def delete_by_source(self, collection: str, source_path: str) -> None:
        self._retry(
            "delete",
            self._client.delete,
            collection_name=collection,
            points_selector=Filter(
                must=[
                    FieldCondition(key="source", match=MatchValue(value=source_path)),
                ]
            ),
        )

    def close(self) -> None:
        self._client.close()
