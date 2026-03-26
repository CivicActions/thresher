from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from thresher.types import FileInfo


@runtime_checkable
class SourceProvider(Protocol):
    """Abstract interface for file storage operations."""

    def list_files(self, prefix: str = "", recursive: bool = True) -> Iterator[FileInfo]: ...

    def download_content(self, path: str) -> bytes: ...

    def download_to_path(self, path: str, local_path: Path) -> Path: ...

    def upload_content(
        self, path: str, data: bytes, if_generation_match: int | None = None
    ) -> None: ...

    def upload_from_path(self, path: str, local_path: Path) -> None: ...

    def exists(self, path: str) -> bool: ...

    def delete(self, path: str) -> None: ...

    def cache_path(self, source_path: str, suffix: str) -> str: ...
