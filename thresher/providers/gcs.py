"""GCS source provider implementation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from thresher.types import FileInfo

logger = logging.getLogger("thresher.providers.gcs")


class GCSSourceProvider:
    """Google Cloud Storage source provider."""

    def __init__(
        self,
        bucket_name: str,
        source_prefix: str = "",
        expanded_prefix: str = "expanded/",
        cache_prefix: str = "cache/",
        queue_prefix: str = "queue/",
    ):
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)
        self.source_prefix = source_prefix
        self.expanded_prefix = expanded_prefix
        self.cache_prefix = cache_prefix
        self.queue_prefix = queue_prefix

    def list_files(self, prefix: str = "", recursive: bool = True) -> Iterator[FileInfo]:
        delimiter = None if recursive else "/"
        for blob in self._bucket.list_blobs(prefix=prefix, delimiter=delimiter):
            if blob.name.endswith("/"):
                continue
            updated = blob.updated or datetime.now(timezone.utc)
            yield FileInfo(
                path=blob.name,
                size=blob.size or 0,
                updated=updated,
                content_type=blob.content_type,
            )

    def download_content(self, path: str) -> bytes:
        blob = self._bucket.blob(path)
        if not blob.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return blob.download_as_bytes()

    def download_to_path(self, path: str, local_path: Path) -> Path:
        blob = self._bucket.blob(path)
        if not blob.exists():
            raise FileNotFoundError(f"File not found: {path}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local_path))
        return local_path

    def upload_content(
        self, path: str, data: bytes, if_generation_match: int | None = None
    ) -> None:
        blob = self._bucket.blob(path)
        try:
            blob.upload_from_string(data, if_generation_match=if_generation_match)
        except PreconditionFailed as e:
            raise FileExistsError(f"File already exists: {path}") from e

    def upload_from_path(self, path: str, local_path: Path) -> None:
        blob = self._bucket.blob(path)
        blob.upload_from_filename(str(local_path))

    def exists(self, path: str) -> bool:
        return self._bucket.blob(path).exists()

    def delete(self, path: str) -> None:
        try:
            self._bucket.blob(path).delete()
        except NotFound:
            pass  # Idempotent

    def cache_path(self, source_path: str, suffix: str) -> str:
        return f"{self.cache_prefix}{source_path}{suffix}"
