# Contract: Destination Provider Interface

**Spec**: [../spec.md](../spec.md) | FR-039, FR-040

## Protocol Definition

```python
from typing import Protocol, runtime_checkable
from dataclasses import dataclass


@dataclass
class IndexChunk:
    """A single chunk ready for indexing with its embedding."""
    point_id: str          # Deterministic UUID from source_path + chunk_index
    text: str              # Chunk text content
    vector: list[float]    # Embedding vector (384 dims for all-MiniLM-L6-v2)
    payload: dict          # Metadata payload (see payload schema below)


@runtime_checkable
class DestinationProvider(Protocol):
    """Abstract interface for vector indexing operations.
    
    All indexing code MUST interact with the vector store exclusively
    through this interface (FR-039).
    """

    def ensure_collection(
        self, name: str, vector_size: int, vector_name: str
    ) -> None:
        """Create collection if it does not exist.
        
        Args:
            name: Collection name (e.g., "vista", "vista-source")
            vector_size: Embedding dimension (e.g., 384)
            vector_name: Named vector identifier (e.g., "fast-all-minilm-l6-v2")
        """
        ...

    def index_chunks(self, collection: str, chunks: list[IndexChunk]) -> None:
        """Batch upsert chunks with embeddings.
        
        Uses upsert semantics — existing points with the same point_id
        are overwritten. MUST use named vectors for compatibility with
        mcp-server-qdrant (FR-018).
        
        Args:
            collection: Target collection name
            chunks: List of chunks to index
            
        Raises:
            ConnectionError: If destination is unreachable
        """
        ...

    def exists_by_hash(
        self, collection: str, source_path: str, content_hash: str
    ) -> bool:
        """Check if a file is already indexed with matching content hash.
        
        Args:
            collection: Collection to search
            source_path: Source provider path of the file
            content_hash: SHA256 hash truncated to 32 hex chars
            
        Returns:
            True if points exist with matching source_path AND content_hash
        """
        ...

    def delete_by_source(self, collection: str, source_path: str) -> None:
        """Delete all points for a source file.
        
        Used when re-indexing a file with updated content.
        
        Args:
            collection: Collection to delete from
            source_path: Source provider path to match
        """
        ...

    def close(self) -> None:
        """Close connection and release resources."""
        ...
```

## Qdrant Payload Schema

Every indexed point includes the following payload fields (FR-018):

| Field | Type | Description |
|-------|------|-------------|
| `source` | str | Source provider path to the original file |
| `source_url` | str | Reconstructed original URL (FR-033) |
| `content_hash` | str | SHA256 hash, truncated to 32 hex chars |
| `chunk_index` | int | 0-based index of this chunk within the file |
| `total_chunks` | int | Total chunks produced from the file |
| `collection` | str | Collection name this point belongs to |
| `file_size` | int | Original file size in bytes |
| `original_format` | str | Original file extension (e.g., ".pdf") |
| `cache_path` | str | Source provider path to cached extraction |
| `indexed_at` | str | ISO 8601 timestamp of indexing |
| `file_type_group` | str | Classified file type group name |
| `chunker_strategy` | str | Chunker that produced this chunk |
| `headings` | list[str] | Document headings context (docling-hybrid) |
| `start_line` | int | Starting line number (code chunkers) |
| `end_line` | int | Ending line number (code chunkers) |

## Point ID Generation

Point IDs MUST be deterministic (FR-019) to enable idempotent upserts:

```python
import uuid

def make_point_id(source_path: str, chunk_index: int) -> str:
    """Deterministic UUID5 from source path and chunk index."""
    namespace = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")  # fixed namespace
    return str(uuid.uuid5(namespace, f"{source_path}:{chunk_index}"))
```

## Qdrant Implementation Notes

The `QdrantDestinationProvider` wraps `qdrant_client.QdrantClient`:

- `ensure_collection` → `client.create_collection()` with `VectorParams(size=vector_size, distance=Distance.COSINE)` using named vectors config
- `index_chunks` → `client.upsert(collection, points=[PointStruct(id=chunk.point_id, vector={vector_name: chunk.vector}, payload=chunk.payload)])`, batched with retry
- `exists_by_hash` → `client.scroll(collection, filter=Filter(must=[...]), limit=1)`
- `delete_by_source` → `client.delete(collection, filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value=source_path))]))`
- `close` → `client.close()`

## Contract Tests

1. `ensure_collection` is idempotent (no error on second call)
2. `index_chunks` + `exists_by_hash` round-trips correctly
3. `index_chunks` with same `point_id` overwrites (upsert semantics)
4. `delete_by_source` removes all points for a source path
5. `exists_by_hash` returns `False` for non-matching hash
6. Named vectors are stored under the correct vector name
