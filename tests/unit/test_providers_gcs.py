"""Unit tests for GCS source provider (all GCS calls mocked)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from thresher.providers.gcs import GCSSourceProvider
from thresher.providers.source import SourceProvider
from thresher.types import FileInfo


@pytest.fixture()
def mock_storage():
    """Patch google.cloud.storage.Client and return (provider, mock_bucket)."""
    with patch("thresher.providers.gcs.storage.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.bucket.return_value = mock_bucket

        provider = GCSSourceProvider(
            bucket_name="test-bucket",
            source_prefix="src/",
            cache_prefix="cache/",
        )
        yield provider, mock_bucket


class TestProtocolConformance:
    def test_implements_source_provider(self, mock_storage):
        provider, _ = mock_storage
        assert isinstance(provider, SourceProvider)


class TestListFiles:
    def test_yields_file_info_objects(self, mock_storage):
        provider, mock_bucket = mock_storage
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        blob1 = MagicMock()
        blob1.name = "docs/readme.md"
        blob1.size = 1024
        blob1.updated = ts
        blob1.content_type = "text/markdown"

        blob2 = MagicMock()
        blob2.name = "docs/guide.pdf"
        blob2.size = 2048
        blob2.updated = ts
        blob2.content_type = "application/pdf"

        mock_bucket.list_blobs.return_value = [blob1, blob2]

        files = list(provider.list_files(prefix="docs/"))
        assert len(files) == 2
        assert all(isinstance(f, FileInfo) for f in files)
        assert files[0].path == "docs/readme.md"
        assert files[0].size == 1024
        assert files[1].content_type == "application/pdf"

    def test_skips_directory_markers(self, mock_storage):
        provider, mock_bucket = mock_storage

        dir_blob = MagicMock()
        dir_blob.name = "docs/"

        file_blob = MagicMock()
        file_blob.name = "docs/file.txt"
        file_blob.size = 100
        file_blob.updated = datetime.now(timezone.utc)
        file_blob.content_type = "text/plain"

        mock_bucket.list_blobs.return_value = [dir_blob, file_blob]

        files = list(provider.list_files(prefix="docs/"))
        assert len(files) == 1
        assert files[0].path == "docs/file.txt"

    def test_non_recursive_uses_delimiter(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_bucket.list_blobs.return_value = []

        list(provider.list_files(prefix="docs/", recursive=False))
        mock_bucket.list_blobs.assert_called_once_with(prefix="docs/", delimiter="/")

    def test_defaults_updated_when_none(self, mock_storage):
        provider, mock_bucket = mock_storage
        blob = MagicMock()
        blob.name = "file.txt"
        blob.size = 50
        blob.updated = None
        blob.content_type = None
        mock_bucket.list_blobs.return_value = [blob]

        files = list(provider.list_files())
        assert files[0].updated is not None


class TestDownloadContent:
    def test_returns_bytes(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_bytes.return_value = b"hello world"
        mock_bucket.blob.return_value = mock_blob

        result = provider.download_content("docs/file.txt")
        assert result == b"hello world"
        mock_blob.download_as_bytes.assert_called_once()

    def test_raises_file_not_found(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        mock_bucket.blob.return_value = mock_blob

        with pytest.raises(FileNotFoundError, match="File not found"):
            provider.download_content("missing.txt")


class TestDownloadToPath:
    def test_downloads_to_local_path(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_bucket.blob.return_value = mock_blob

        local = Path("local/dir/file.txt")
        with patch.object(Path, "mkdir"):
            result = provider.download_to_path("remote/file.txt", local)

        mock_blob.download_to_filename.assert_called_once_with(str(local))
        assert result == local

    def test_raises_file_not_found(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        mock_bucket.blob.return_value = mock_blob

        with pytest.raises(FileNotFoundError):
            provider.download_to_path("missing.txt", Path("local.txt"))


class TestUploadContent:
    def test_uploads_bytes(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        provider.upload_content("out/file.txt", b"data")
        mock_blob.upload_from_string.assert_called_once_with(b"data", if_generation_match=None)

    def test_raises_file_exists_on_precondition_failed(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.upload_from_string.side_effect = PreconditionFailed("exists")
        mock_bucket.blob.return_value = mock_blob

        with pytest.raises(FileExistsError, match="File already exists"):
            provider.upload_content("out/file.txt", b"data", if_generation_match=0)

    def test_passes_generation_match(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        provider.upload_content("out/file.txt", b"data", if_generation_match=0)
        mock_blob.upload_from_string.assert_called_once_with(b"data", if_generation_match=0)


class TestUploadFromPath:
    def test_uploads_from_local_path(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        local = Path("local/file.txt")
        provider.upload_from_path("remote/file.txt", local)
        mock_blob.upload_from_filename.assert_called_once_with(str(local))


class TestExists:
    def test_delegates_to_blob_exists(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_bucket.blob.return_value = mock_blob

        assert provider.exists("file.txt") is True
        mock_blob.exists.assert_called_once()

    def test_returns_false_when_not_exists(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        mock_bucket.blob.return_value = mock_blob

        assert provider.exists("missing.txt") is False


class TestDelete:
    def test_deletes_blob(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        provider.delete("file.txt")
        mock_blob.delete.assert_called_once()

    def test_idempotent_on_not_found(self, mock_storage):
        provider, mock_bucket = mock_storage
        mock_blob = MagicMock()
        mock_blob.delete.side_effect = NotFound("not found")
        mock_bucket.blob.return_value = mock_blob

        # Should not raise
        provider.delete("missing.txt")


class TestCachePath:
    def test_prepends_cache_prefix(self, mock_storage):
        provider, _ = mock_storage
        result = provider.cache_path("docs/file.pdf", ".md")
        assert result == "cache/docs/file.pdf.md"

    def test_with_different_suffix(self, mock_storage):
        provider, _ = mock_storage
        result = provider.cache_path("data/report.csv", ".json")
        assert result == "cache/data/report.csv.json"

    def test_empty_source_path(self, mock_storage):
        provider, _ = mock_storage
        result = provider.cache_path("", ".txt")
        assert result == "cache/.txt"
