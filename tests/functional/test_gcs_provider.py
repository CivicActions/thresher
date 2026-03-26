"""Functional tests for GCSSourceProvider against fake-gcs-server."""

from __future__ import annotations

import os

import pytest

from tests.functional.conftest import FAKE_GCS_URL, GCS_BUCKET

pytestmark = pytest.mark.functional


@pytest.fixture
def gcs_provider(clean_bucket):
    """Create a GCSSourceProvider pointing at the fake-gcs-server."""
    os.environ["STORAGE_EMULATOR_HOST"] = FAKE_GCS_URL
    from thresher.providers.gcs import GCSSourceProvider

    return GCSSourceProvider(
        bucket_name=GCS_BUCKET,
        source_prefix="source/",
        expanded_prefix="expanded/",
        cache_prefix="cache/",
        queue_prefix="queue/",
    )


class TestUploadAndDownload:
    def test_upload_content_and_download(self, gcs_provider):
        gcs_provider.upload_content("source/hello.txt", b"Hello, world!")
        result = gcs_provider.download_content("source/hello.txt")
        assert result == b"Hello, world!"

    def test_upload_from_path_and_download_to_path(self, gcs_provider, tmp_path):
        local_src = tmp_path / "upload.txt"
        local_src.write_bytes(b"File from path")
        gcs_provider.upload_from_path("source/from_path.txt", local_src)

        local_dst = tmp_path / "download.txt"
        gcs_provider.download_to_path("source/from_path.txt", local_dst)
        assert local_dst.read_bytes() == b"File from path"

    def test_download_nonexistent_raises(self, gcs_provider):
        with pytest.raises(FileNotFoundError):
            gcs_provider.download_content("source/no_such_file.txt")

    def test_upload_overwrite(self, gcs_provider):
        gcs_provider.upload_content("source/overwrite.txt", b"v1")
        gcs_provider.upload_content("source/overwrite.txt", b"v2")
        assert gcs_provider.download_content("source/overwrite.txt") == b"v2"


class TestListFiles:
    def test_list_empty_prefix(self, gcs_provider):
        files = list(gcs_provider.list_files(prefix="source/"))
        assert files == []

    def test_list_returns_uploaded_files(self, gcs_provider):
        gcs_provider.upload_content("source/a.txt", b"a")
        gcs_provider.upload_content("source/b.txt", b"b")
        gcs_provider.upload_content("other/c.txt", b"c")

        paths = [fi.path for fi in gcs_provider.list_files(prefix="source/")]
        assert sorted(paths) == ["source/a.txt", "source/b.txt"]

    def test_list_recursive(self, gcs_provider):
        gcs_provider.upload_content("source/dir/nested.txt", b"n")
        gcs_provider.upload_content("source/top.txt", b"t")

        paths = [fi.path for fi in gcs_provider.list_files(prefix="source/")]
        assert "source/dir/nested.txt" in paths
        assert "source/top.txt" in paths

    def test_list_file_info_has_size(self, gcs_provider):
        gcs_provider.upload_content("source/sized.txt", b"12345")
        infos = list(gcs_provider.list_files(prefix="source/"))
        assert len(infos) == 1
        assert infos[0].size == 5


class TestExistsAndDelete:
    def test_exists_true(self, gcs_provider):
        gcs_provider.upload_content("source/exists.txt", b"here")
        assert gcs_provider.exists("source/exists.txt") is True

    def test_exists_false(self, gcs_provider):
        assert gcs_provider.exists("source/nope.txt") is False

    def test_delete(self, gcs_provider):
        gcs_provider.upload_content("source/delete_me.txt", b"bye")
        gcs_provider.delete("source/delete_me.txt")
        assert gcs_provider.exists("source/delete_me.txt") is False

    def test_delete_nonexistent_is_idempotent(self, gcs_provider):
        gcs_provider.delete("source/never_existed.txt")  # Should not raise


class TestAtomicWrite:
    def test_if_generation_match_zero_creates(self, gcs_provider):
        gcs_provider.upload_content("queue/batch.json", b"data", if_generation_match=0)
        assert gcs_provider.download_content("queue/batch.json") == b"data"

    def test_if_generation_match_zero_fails_on_existing(self, gcs_provider):
        gcs_provider.upload_content("queue/claim.json", b"first")
        with pytest.raises(FileExistsError):
            gcs_provider.upload_content("queue/claim.json", b"second", if_generation_match=0)


class TestCachePath:
    def test_cache_path(self, gcs_provider):
        result = gcs_provider.cache_path("source/doc.pdf", ".md")
        assert result == "cache/source/doc.pdf.md"
