from __future__ import annotations

from typing import Protocol, runtime_checkable

from thresher.types import IndexChunk


@runtime_checkable
class DestinationProvider(Protocol):
    """Abstract interface for vector indexing operations."""

    def ensure_collection(self, name: str, vector_size: int, vector_name: str) -> None: ...

    def index_chunks(self, collection: str, chunks: list[IndexChunk]) -> None: ...

    def delete_by_source(self, collection: str, source_path: str) -> None: ...

    def close(self) -> None: ...

    def set_indexing_threshold(self, collection: str, threshold: int) -> None: ...
