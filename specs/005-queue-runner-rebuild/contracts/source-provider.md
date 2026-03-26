# Contract: Source Provider Interface

**Spec**: [../spec.md](../spec.md) | FR-038, FR-040

## Protocol Definition

```python
from typing import Protocol, Iterator, runtime_checkable
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass


@dataclass
class FileInfo:
    """Metadata about a file on the source provider."""
    path: str
    size: int
    updated: datetime
    content_type: str | None = None


@runtime_checkable
class SourceProvider(Protocol):
    """Abstract interface for file storage operations.
    
    All controller and runner code MUST interact with file storage
    exclusively through this interface (FR-038).
    """

    def list_files(self, prefix: str = "", recursive: bool = True) -> Iterator[FileInfo]:
        """List files with optional prefix filtering.
        
        Args:
            prefix: Path prefix to filter by (e.g., "expanded/")
            recursive: Whether to recurse into subdirectories
            
        Yields:
            FileInfo for each matching file
        """
        ...

    def download_content(self, path: str) -> bytes:
        """Download file content as bytes.
        
        Args:
            path: Full path on the provider
            
        Returns:
            File content as bytes
            
        Raises:
            FileNotFoundError: If file does not exist
        """
        ...

    def download_to_path(self, path: str, local_path: Path) -> Path:
        """Download file to a local filesystem path.
        
        Args:
            path: Full path on the provider
            local_path: Local destination path
            
        Returns:
            The local_path where file was written
            
        Raises:
            FileNotFoundError: If file does not exist
        """
        ...

    def upload_content(
        self, path: str, data: bytes, if_generation_match: int | None = None
    ) -> None:
        """Upload content to a path.
        
        Args:
            path: Destination path on the provider
            data: Content bytes to upload
            if_generation_match: Conditional write. 0 = create-only (fail if exists).
                                 None = unconditional write.
                                 
        Raises:
            FileExistsError: If if_generation_match=0 and file already exists
        """
        ...

    def upload_from_path(self, path: str, local_path: Path) -> None:
        """Upload a local file to a path.
        
        Args:
            path: Destination path on the provider
            local_path: Local file to upload
        """
        ...

    def exists(self, path: str) -> bool:
        """Check if a file exists.
        
        Args:
            path: Path to check
            
        Returns:
            True if file exists
        """
        ...

    def delete(self, path: str) -> None:
        """Delete a file. Idempotent — no error if file doesn't exist.
        
        Args:
            path: Path to delete
        """
        ...

    def cache_path(self, source_path: str, suffix: str) -> str:
        """Compute the cache path for a source file.
        
        Args:
            source_path: Original source file path
            suffix: Cache file suffix (e.g., ".md", ".docling.json")
            
        Returns:
            Full path to the cache file on the provider
            
        Example:
            cache_path("docs/report.pdf", ".md") → "cache/docs/report.pdf.md"
        """
        ...
```

## GCS Implementation Notes

The `GCSSourceProvider` implementation wraps `google.cloud.storage.Client`:

- `list_files` → `bucket.list_blobs(prefix=prefix)`
- `download_content` → `blob.download_as_bytes()`
- `upload_content(if_generation_match=0)` → `blob.upload_from_string(data, if_generation_match=0)`, catches `google.api_core.exceptions.PreconditionFailed` → raises `FileExistsError`
- `delete` → `blob.delete()`, catches `google.api_core.exceptions.NotFound` → no-op
- `cache_path` → prepends configured cache prefix (e.g., `cache/`)

## Contract Tests

Contract tests verify that any `SourceProvider` implementation conforms to the interface:

1. `upload_content` + `download_content` round-trips data correctly
2. `upload_content(if_generation_match=0)` raises `FileExistsError` on second call
3. `list_files` returns uploaded files with correct metadata
4. `delete` is idempotent (no error on non-existent path)
5. `exists` returns `True` after upload, `False` after delete
6. `cache_path` returns a path under the configured cache prefix
