"""Unit tests for archive expansion (ArchiveExpander) and scanner integration."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest

from thresher.config import Config, GCSConfig, ProcessingConfig, SourceConfig
from thresher.controller.archive_expander import (
    ArchiveExpander,
    _archive_stem,
    _detect_archive_type_from_bytes,
    _should_skip_member,
    is_archive,
)
from thresher.controller.scanner import scan_files
from thresher.types import ChunkerConfig, FileInfo, FileTypeGroup

# ---------------------------------------------------------------------------
# Helpers — build real archives in memory
# ---------------------------------------------------------------------------


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_gz(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.open(buf, "wb") as f:
        f.write(data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixture — mock SourceProvider backed by an in-memory dict
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_source():
    """MagicMock SourceProvider with dict-backed storage."""
    storage: dict[str, bytes] = {}
    source = MagicMock()

    def _download_to_path(path: str, local_path: Path) -> Path:
        Path(local_path).write_bytes(storage[path])
        return Path(local_path)

    def _upload_from_path(remote_path: str, local_path: Path) -> None:
        storage[remote_path] = Path(local_path).read_bytes()

    def _upload_content(path: str, data: bytes, if_generation_match: object = None) -> None:
        storage[path] = data

    def _list_files(prefix: str = "", recursive: bool = True) -> Iterator[FileInfo]:
        return iter(
            [
                FileInfo(path=p, size=len(d), updated=datetime.now())
                for p, d in sorted(storage.items())
                if p.startswith(prefix)
            ]
        )

    source.download_to_path.side_effect = _download_to_path
    source.upload_from_path.side_effect = _upload_from_path
    source.upload_content.side_effect = _upload_content
    source.exists.side_effect = lambda path: path in storage
    source.download_content.side_effect = lambda path: storage[path]
    source.list_files.side_effect = _list_files
    source._storage = storage
    return source


# ---------------------------------------------------------------------------
# is_archive
# ---------------------------------------------------------------------------


class TestIsArchive:
    @pytest.mark.parametrize(
        "path",
        [
            "data/files.zip",
            "archive.tar.gz",
            "archive.tgz",
            "archive.tar.bz2",
            "archive.tar.xz",
            "data.tar",
            "file.gz",
            "file.bz2",
            "file.xz",
        ],
    )
    def test_recognized_extensions(self, path: str) -> None:
        assert is_archive(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "readme.txt",
            "image.png",
            "app.jar",
            "report.pdf",
            "module.war",
            "lib.whl",
        ],
    )
    def test_non_archive_extensions(self, path: str) -> None:
        assert is_archive(path) is False

    def test_case_insensitive(self) -> None:
        assert is_archive("ARCHIVE.ZIP") is True
        assert is_archive("Data.TAR.GZ") is True

    def test_jar_excluded_by_default(self) -> None:
        exclude = frozenset({".jar", ".war", ".whl", ".egg"})
        assert is_archive("lib.jar", exclude_extensions=exclude) is False
        assert is_archive("app.war", exclude_extensions=exclude) is False

    def test_jar_not_excluded_without_list(self) -> None:
        # .jar is not in _ARCHIVE_EXTENSIONS, so it's False either way
        assert is_archive("lib.jar") is False

    def test_custom_exclude_blocks_zip(self) -> None:
        # .zip is normally an archive, but can be excluded
        exclude = frozenset({".zip"})
        assert is_archive("data.zip", exclude_extensions=exclude) is False

    def test_exclude_does_not_block_other_archives(self) -> None:
        exclude = frozenset({".jar"})
        assert is_archive("data.zip", exclude_extensions=exclude) is True
        assert is_archive("data.tar.gz", exclude_extensions=exclude) is True

    def test_extensionless_zip_by_content(self) -> None:
        content = _make_zip({"hello.txt": b"hello"})
        assert is_archive("data/noext_file", content=content) is True

    def test_extensionless_tar_gz_by_content(self) -> None:
        content = _make_tar_gz({"a.txt": b"a"})
        assert is_archive("archive_file", content=content) is True

    def test_extensionless_gz_by_content(self) -> None:
        content = _make_gz(b"hello world")
        assert is_archive("compressed_data", content=content) is True

    def test_extensionless_non_archive_by_content(self) -> None:
        assert is_archive("some_file", content=b"just plain text content") is False

    def test_extensionless_too_short_content(self) -> None:
        assert is_archive("tiny", content=b"PK") is False


class TestDetectArchiveTypeFromBytes:
    def test_zip_magic(self) -> None:
        assert _detect_archive_type_from_bytes(b"PK\x03\x04rest...") == "zip"

    def test_gz_magic(self) -> None:
        assert _detect_archive_type_from_bytes(b"\x1f\x8b\x08more...") == "gz"

    def test_bz2_magic(self) -> None:
        assert _detect_archive_type_from_bytes(b"BZh9more...") == "bz2"

    def test_xz_magic(self) -> None:
        assert _detect_archive_type_from_bytes(b"\xfd7zXZ\x00more") == "xz"

    def test_tar_magic(self) -> None:
        header = b"\x00" * 257 + b"ustar" + b"\x00" * 250
        assert _detect_archive_type_from_bytes(header) == "tar"

    def test_no_match(self) -> None:
        assert _detect_archive_type_from_bytes(b"just text content here pad") is None


# ---------------------------------------------------------------------------
# _should_skip_member
# ---------------------------------------------------------------------------


class TestShouldSkipMember:
    def test_empty_name(self) -> None:
        assert _should_skip_member("") is True

    def test_directory_entry(self) -> None:
        assert _should_skip_member("subdir/") is True

    def test_macosx_resource_fork(self) -> None:
        assert _should_skip_member("__MACOSX/file.txt") is True
        assert _should_skip_member("dir/__MACOSX/._thing") is True

    def test_dot_underscore_files(self) -> None:
        assert _should_skip_member("._hidden") is True
        assert _should_skip_member("subdir/._resource") is True

    def test_junk_files(self) -> None:
        assert _should_skip_member("Thumbs.db") is True
        assert _should_skip_member("desktop.ini") is True
        assert _should_skip_member(".DS_Store") is True
        assert _should_skip_member("subdir/.DS_Store") is True

    def test_non_extractable_archives(self) -> None:
        exclude = frozenset({".jar", ".war", ".whl", ".egg", ".apk", ".ipa"})
        assert _should_skip_member("lib.jar", exclude) is True
        assert _should_skip_member("app.war", exclude) is True
        assert _should_skip_member("pkg.whl", exclude) is True
        assert _should_skip_member("pkg.egg", exclude) is True

    def test_non_extractable_without_exclude_list(self) -> None:
        # Without an exclude list, .jar etc are NOT skipped
        assert _should_skip_member("lib.jar") is False
        assert _should_skip_member("app.war") is False

    def test_custom_exclude_extensions(self) -> None:
        custom = frozenset({".ear", ".aar"})
        assert _should_skip_member("lib.ear", custom) is True
        assert _should_skip_member("lib.aar", custom) is True
        assert _should_skip_member("lib.jar", custom) is False

    def test_normal_files_not_skipped(self) -> None:
        assert _should_skip_member("readme.txt") is False
        assert _should_skip_member("subdir/data.csv") is False
        assert _should_skip_member("report.pdf") is False


# ---------------------------------------------------------------------------
# _archive_stem
# ---------------------------------------------------------------------------


class TestArchiveStem:
    def test_zip(self) -> None:
        assert _archive_stem("data/files.zip") == "data/files"

    def test_tar_gz(self) -> None:
        assert _archive_stem("data/archive.tar.gz") == "data/archive"

    def test_tgz(self) -> None:
        assert _archive_stem("data/archive.tgz") == "data/archive"

    def test_tar_bz2(self) -> None:
        assert _archive_stem("archive.tar.bz2") == "archive"

    def test_standalone_gz(self) -> None:
        assert _archive_stem("file.txt.gz") == "file.txt"

    def test_no_parent(self) -> None:
        assert _archive_stem("simple.zip") == "simple"


# ---------------------------------------------------------------------------
# ZIP expansion
# ---------------------------------------------------------------------------


class TestZipExpansion:
    def test_basic_zip(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip({"hello.txt": b"hello", "sub/world.txt": b"world"})
        mock_source._storage["data/test.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        files = [FileInfo(path="data/test.zip", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        paths = [r["path"] for r in results]
        assert "expanded/data/test/hello.txt" in paths
        assert "expanded/data/test/sub/world.txt" in paths
        assert all(r["source_type"] == "expanded" for r in results)
        assert all(r["archive_path"] == "data/test.zip" for r in results)

    def test_uploaded_content_matches(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip({"readme.md": b"# Title"})
        mock_source._storage["pkg.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/")
        expander.expand_archives(
            [FileInfo(path="pkg.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        assert mock_source._storage["exp/pkg/readme.md"] == b"# Title"


# ---------------------------------------------------------------------------
# TAR.GZ expansion
# ---------------------------------------------------------------------------


class TestTarGzExpansion:
    def test_basic_tar_gz(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_tar_gz({"a.txt": b"aaa", "b.txt": b"bbb"})
        mock_source._storage["archive.tar.gz"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        results = expander.expand_archives(
            [FileInfo(path="archive.tar.gz", size=len(archive_bytes), updated=datetime.now())]
        )

        paths = sorted(r["path"] for r in results)
        assert paths == ["expanded/archive/a.txt", "expanded/archive/b.txt"]

    def test_tgz_alias(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_tar_gz({"file.txt": b"data"})
        mock_source._storage["bundle.tgz"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/")
        results = expander.expand_archives(
            [FileInfo(path="bundle.tgz", size=len(archive_bytes), updated=datetime.now())]
        )

        assert len(results) == 1
        assert results[0]["path"] == "exp/bundle/file.txt"


# ---------------------------------------------------------------------------
# Standalone GZ expansion
# ---------------------------------------------------------------------------


class TestStandaloneGzExpansion:
    def test_gz_produces_stem(self, mock_source: MagicMock) -> None:
        gz_bytes = _make_gz(b"plain text content")
        mock_source._storage["logs/output.txt.gz"] = gz_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        results = expander.expand_archives(
            [FileInfo(path="logs/output.txt.gz", size=len(gz_bytes), updated=datetime.now())]
        )

        assert len(results) == 1
        assert results[0]["path"] == "expanded/logs/output.txt/output.txt"

    def test_gz_content(self, mock_source: MagicMock) -> None:
        gz_bytes = _make_gz(b"hello gzip")
        mock_source._storage["file.txt.gz"] = gz_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="out/")
        expander.expand_archives(
            [FileInfo(path="file.txt.gz", size=len(gz_bytes), updated=datetime.now())]
        )

        assert mock_source._storage["out/file.txt/file.txt"] == b"hello gzip"


# ---------------------------------------------------------------------------
# Recursive expansion
# ---------------------------------------------------------------------------


class TestRecursiveExpansion:
    def test_nested_zip_in_zip(self, mock_source: MagicMock) -> None:
        inner_zip = _make_zip({"deep.txt": b"deep content"})
        outer_zip = _make_zip({"inner.zip": inner_zip, "top.txt": b"top"})
        mock_source._storage["data/outer.zip"] = outer_zip

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/", max_depth=2)
        results = expander.expand_archives(
            [FileInfo(path="data/outer.zip", size=len(outer_zip), updated=datetime.now())]
        )

        paths = sorted(r["path"] for r in results)
        assert "expanded/data/outer/top.txt" in paths
        assert "expanded/data/outer/inner/deep.txt" in paths
        assert all(r["archive_path"] == "data/outer.zip" for r in results)

    def test_nested_tar_in_zip(self, mock_source: MagicMock) -> None:
        inner_tar = _make_tar_gz({"nested.csv": b"a,b,c"})
        outer_zip = _make_zip({"bundle.tar.gz": inner_tar})
        mock_source._storage["pkg.zip"] = outer_zip

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/", max_depth=2)
        results = expander.expand_archives(
            [FileInfo(path="pkg.zip", size=len(outer_zip), updated=datetime.now())]
        )

        paths = [r["path"] for r in results]
        assert "exp/pkg/bundle/nested.csv" in paths


# ---------------------------------------------------------------------------
# Max depth limit
# ---------------------------------------------------------------------------


class TestMaxDepthLimit:
    def test_depth_zero_expands_nothing(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip({"file.txt": b"data"})
        mock_source._storage["test.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/", max_depth=0)
        results = expander.expand_archives(
            [FileInfo(path="test.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        assert results == []

    def test_depth_one_no_recursion(self, mock_source: MagicMock) -> None:
        inner_zip = _make_zip({"deep.txt": b"deep"})
        outer_zip = _make_zip({"inner.zip": inner_zip, "top.txt": b"top"})
        mock_source._storage["outer.zip"] = outer_zip

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/", max_depth=1)
        results = expander.expand_archives(
            [FileInfo(path="outer.zip", size=len(outer_zip), updated=datetime.now())]
        )

        paths = [r["path"] for r in results]
        # top.txt is extracted, inner.zip is uploaded but NOT recursively expanded
        assert "exp/outer/top.txt" in paths
        assert "exp/outer/inner.zip" in paths
        # deep.txt is NOT present (depth limit prevents recursion)
        assert not any("deep.txt" in p for p in paths)


# ---------------------------------------------------------------------------
# Hidden file / resource fork filtering
# ---------------------------------------------------------------------------


class TestHiddenFileFiltering:
    def test_macosx_folder_skipped(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip(
            {
                "readme.txt": b"hello",
                "__MACOSX/readme.txt": b"resource fork",
                "__MACOSX/._readme.txt": b"xattr",
            }
        )
        mock_source._storage["test.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/")
        results = expander.expand_archives(
            [FileInfo(path="test.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        paths = [r["path"] for r in results]
        assert len(paths) == 1
        assert paths[0] == "exp/test/readme.txt"

    def test_dot_underscore_files_skipped(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip({"doc.pdf": b"pdf", "._doc.pdf": b"resource"})
        mock_source._storage["test.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/")
        results = expander.expand_archives(
            [FileInfo(path="test.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        assert len(results) == 1
        assert results[0]["path"] == "exp/test/doc.pdf"

    def test_junk_files_skipped(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip(
            {
                "data.csv": b"a,b",
                "Thumbs.db": b"thumbs",
                "desktop.ini": b"ini",
                ".DS_Store": b"ds",
            }
        )
        mock_source._storage["test.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/")
        results = expander.expand_archives(
            [FileInfo(path="test.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        assert len(results) == 1
        assert results[0]["path"] == "exp/test/data.csv"


# ---------------------------------------------------------------------------
# Non-extractable archive skip
# ---------------------------------------------------------------------------


class TestNonExtractableArchiveSkip:
    @pytest.mark.parametrize("ext", [".jar", ".war", ".whl", ".egg"])
    def test_skipped_inside_archive(self, mock_source: MagicMock, ext: str) -> None:
        archive_bytes = _make_zip({"readme.txt": b"hello", f"lib{ext}": b"binary blob"})
        mock_source._storage["test.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="exp/")
        results = expander.expand_archives(
            [FileInfo(path="test.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        paths = [r["path"] for r in results]
        assert len(paths) == 1
        assert paths[0] == "exp/test/readme.txt"


# ---------------------------------------------------------------------------
# Idempotent expansion (expansion records)
# ---------------------------------------------------------------------------


class TestIdempotentExpansion:
    def test_skips_when_record_exists(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip({"a.txt": b"aaa"})
        mock_source._storage["data/test.zip"] = archive_bytes

        # Pre-populate expansion record and expanded file
        record = {
            "archive_path": "data/test.zip",
            "expansion_folder": "expanded/data/test",
            "member_count": 1,
            "expanded_at": 1700000000.0,
            "archive_hash": "abc123",
        }
        mock_source._storage["expanded/data/test/.expansion-record.json"] = json.dumps(
            record
        ).encode()
        mock_source._storage["expanded/data/test/a.txt"] = b"aaa"

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        results = expander.expand_archives(
            [FileInfo(path="data/test.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        # Should return the existing expanded file without re-downloading
        assert len(results) == 1
        assert results[0]["path"] == "expanded/data/test/a.txt"
        # download_to_path should NOT have been called (skipped expansion)
        mock_source.download_to_path.assert_not_called()

    def test_saves_record_after_expansion(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip({"file.txt": b"content"})
        mock_source._storage["pkg.zip"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        expander.expand_archives(
            [FileInfo(path="pkg.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        record_path = "expanded/pkg/.expansion-record.json"
        assert record_path in mock_source._storage
        record = json.loads(mock_source._storage[record_path])
        assert record["archive_path"] == "pkg.zip"
        assert record["member_count"] == 1
        assert record["archive_hash"] is not None

    def test_record_excludes_expansion_record_file(self, mock_source: MagicMock) -> None:
        """When re-listing, the .expansion-record.json file itself is excluded."""
        archive_bytes = _make_zip({"a.txt": b"a"})
        mock_source._storage["test.zip"] = archive_bytes
        record = {
            "archive_path": "test.zip",
            "expansion_folder": "expanded/test",
            "member_count": 1,
            "expanded_at": 1700000000.0,
            "archive_hash": None,
        }
        mock_source._storage["expanded/test/.expansion-record.json"] = json.dumps(record).encode()
        mock_source._storage["expanded/test/a.txt"] = b"a"

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        results = expander.expand_archives(
            [FileInfo(path="test.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        paths = [r["path"] for r in results]
        assert "expanded/test/.expansion-record.json" not in paths
        assert "expanded/test/a.txt" in paths


# ---------------------------------------------------------------------------
# Scanner integration with archives
# ---------------------------------------------------------------------------


@pytest.fixture
def scanner_config():
    """Config for scanner integration tests."""
    cfg = Config()
    cfg.source = SourceConfig(
        provider="gcs",
        gcs=GCSConfig(
            bucket="test-bucket",
            source_prefix="data/",
            expanded_prefix="expanded/",
        ),
    )
    cfg.processing = ProcessingConfig(archive_depth=2)
    cfg.file_type_groups = {
        "documents": FileTypeGroup(
            name="documents",
            extensions=[".pdf", ".docx", ".txt"],
            extractor="docling",
            chunker=ChunkerConfig(strategy="docling-hybrid"),
        ),
        "code": FileTypeGroup(
            name="code",
            extensions=[".m", ".py"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="chonkie-recursive"),
        ),
    }
    return cfg


class TestScannerArchiveIntegration:
    def test_archives_expanded_and_classified(
        self, mock_source: MagicMock, scanner_config: Config
    ) -> None:
        archive_bytes = _make_zip({"readme.txt": b"hello", "code.py": b"print(1)"})
        mock_source._storage["data/bundle.zip"] = archive_bytes
        mock_source._storage["data/direct.m"] = b"ROUTINE"

        # list_files must return both direct files and archives
        base_files = [
            FileInfo(path="data/direct.m", size=7, updated=datetime.now()),
            FileInfo(path="data/bundle.zip", size=len(archive_bytes), updated=datetime.now()),
        ]
        call_count = {"n": 0}
        original_list = mock_source.list_files.side_effect

        def _list_files(prefix: str = "", recursive: bool = True) -> Iterator[FileInfo]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return iter(base_files)
            # Subsequent calls (from _list_existing_expanded) use storage
            return original_list(prefix=prefix, recursive=recursive)

        mock_source.list_files.side_effect = _list_files

        items = scan_files(mock_source, scanner_config)

        direct = [i for i in items if i["source_type"] == "direct"]
        expanded = [i for i in items if i["source_type"] == "expanded"]

        assert len(direct) == 1
        assert direct[0]["path"] == "data/direct.m"
        assert direct[0]["file_type_group"] == "code"

        assert len(expanded) == 2
        exp_paths = sorted(e["path"] for e in expanded)
        assert "expanded/data/bundle/code.py" in exp_paths
        assert "expanded/data/bundle/readme.txt" in exp_paths
        for e in expanded:
            assert e["archive_path"] == "data/bundle.zip"

    def test_unclassified_expanded_files_skipped(
        self, mock_source: MagicMock, scanner_config: Config
    ) -> None:
        """Expanded files that don't match any group are skipped."""
        archive_bytes = _make_zip({"image.png": b"PNG"})
        mock_source._storage["data/imgs.zip"] = archive_bytes

        mock_source.list_files.side_effect = lambda prefix="", recursive=True: iter(
            [FileInfo(path="data/imgs.zip", size=len(archive_bytes), updated=datetime.now())]
        )

        items = scan_files(mock_source, scanner_config)

        # .png is not in any configured group → skipped
        assert items == []

    def test_no_archives_same_behaviour(
        self, mock_source: MagicMock, scanner_config: Config
    ) -> None:
        """When there are no archives the scanner behaves identically to before."""
        mock_source.list_files.side_effect = lambda prefix="", recursive=True: iter(
            [
                FileInfo(path="data/file.txt", size=10, updated=datetime.now()),
                FileInfo(path="data/code.py", size=20, updated=datetime.now()),
            ]
        )

        items = scan_files(mock_source, scanner_config)

        assert len(items) == 2
        assert all(i["source_type"] == "direct" for i in items)


# ---------------------------------------------------------------------------
# Extensionless archive expansion (magic-byte detection)
# ---------------------------------------------------------------------------


class TestExtensionlessArchiveExpansion:
    def test_extensionless_zip_detected_and_expanded(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_zip({"doc.txt": b"hello"})
        mock_source._storage["data/mystery_file"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        files = [
            FileInfo(path="data/mystery_file", size=len(archive_bytes), updated=datetime.now())
        ]
        results = expander.expand_archives(files)

        paths = [r["path"] for r in results]
        assert "expanded/data/mystery_file/doc.txt" in paths

    def test_extensionless_tar_gz_detected_and_expanded(self, mock_source: MagicMock) -> None:
        archive_bytes = _make_tar_gz({"a.py": b"print('hi')"})
        mock_source._storage["bundle_noext"] = archive_bytes

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        files = [FileInfo(path="bundle_noext", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        paths = [r["path"] for r in results]
        assert len(paths) == 1
        assert "a.py" in paths[0]

    def test_extensionless_non_archive_not_expanded(self, mock_source: MagicMock) -> None:
        mock_source._storage["data/readme"] = b"Just a plain text file without extension"

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        files = [FileInfo(path="data/readme", size=40, updated=datetime.now())]
        results = expander.expand_archives(files)

        assert results == []

    def test_extension_file_not_probed(self, mock_source: MagicMock) -> None:
        """Files with a non-archive extension should NOT trigger content probe."""
        mock_source._storage["data/report.pdf"] = b"%PDF-1.4 ..."

        expander = ArchiveExpander(mock_source, expanded_prefix="expanded/")
        files = [FileInfo(path="data/report.pdf", size=50, updated=datetime.now())]
        results = expander.expand_archives(files)

        assert results == []
        # download_content should NOT be called for .pdf files
        mock_source.download_content.assert_not_called()

    def test_jar_excluded_from_expansion(self, mock_source: MagicMock) -> None:
        """Jar files (zip-based) should be excluded from archive expansion."""
        archive_bytes = _make_zip({"META-INF/MANIFEST.MF": b"Manifest-Version: 1.0"})
        mock_source._storage["libs/mylib.jar"] = archive_bytes

        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
            exclude_extensions=[".jar", ".war"],
        )
        files = [FileInfo(path="libs/mylib.jar", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        assert results == []

    def test_custom_exclude_prevents_expansion(self, mock_source: MagicMock) -> None:
        """Custom exclude list prevents expansion of specified extensions."""
        archive_bytes = _make_zip({"data.csv": b"a,b,c"})
        mock_source._storage["data/bundle.xyz"] = archive_bytes

        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
            exclude_extensions=[".xyz"],
        )
        files = [FileInfo(path="data/bundle.xyz", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        assert results == []

    def test_empty_exclude_allows_all(self, mock_source: MagicMock) -> None:
        """Empty exclude list allows all archive types including .jar."""
        archive_bytes = _make_zip({"readme.txt": b"hello"})
        mock_source._storage["libs/mylib.jar"] = archive_bytes

        # .jar is not in _ARCHIVE_EXTENSIONS, so it won't be detected by extension
        # But this test confirms the exclude list doesn't interfere with normal archives
        mock_source._storage["data/test.zip"] = archive_bytes
        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
            exclude_extensions=[],
        )
        files = [FileInfo(path="data/test.zip", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        assert len(results) == 1



# ---------------------------------------------------------------------------
# Concurrent upload tests
# ---------------------------------------------------------------------------


class TestConcurrentUploads:
    """Tests for batched concurrent upload functionality."""

    def test_batch_upload_with_pool(self, mock_source: MagicMock) -> None:
        """Verify concurrent upload uses configured batch size."""
        archive_bytes = _make_zip({f"file{i}.txt": f"data{i}".encode() for i in range(10)})
        mock_source._storage["data/big.zip"] = archive_bytes

        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
            upload_batch_size=5,
        )
        files = [FileInfo(path="data/big.zip", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        assert len(results) == 10
        # All files should be uploaded
        uploaded_paths = [r["path"] for r in results]
        for i in range(10):
            assert any(f"file{i}.txt" in p for p in uploaded_paths)

    def test_sequential_fallback_with_batch_size_one(self, mock_source: MagicMock) -> None:
        """batch_size=1 should use sequential upload (no thread pool)."""
        archive_bytes = _make_zip({"a.txt": b"aaa", "b.txt": b"bbb"})
        mock_source._storage["data/test.zip"] = archive_bytes

        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
            upload_batch_size=1,
        )
        files = [FileInfo(path="data/test.zip", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        assert len(results) == 2

    def test_upload_retry_on_failure(self, mock_source: MagicMock) -> None:
        """Upload should retry on transient failure."""
        archive_bytes = _make_zip({"file.txt": b"data"})
        mock_source._storage["data/test.zip"] = archive_bytes

        call_count = [0]
        original_upload = mock_source.upload_from_path.side_effect

        def flaky_upload(remote_path, local_path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("transient failure")
            return original_upload(remote_path, local_path)

        mock_source.upload_from_path.side_effect = flaky_upload

        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
            upload_batch_size=1,
        )
        files = [FileInfo(path="data/test.zip", size=len(archive_bytes), updated=datetime.now())]
        results = expander.expand_archives(files)

        assert len(results) == 1
        assert call_count[0] >= 2  # at least one retry

    def test_upload_failure_after_retries_raises(self, mock_source: MagicMock) -> None:
        """Upload should raise after exhausting retries."""
        archive_bytes = _make_zip({"file.txt": b"data"})
        mock_source._storage["data/test.zip"] = archive_bytes

        mock_source.upload_from_path.side_effect = ConnectionError("permanent failure")

        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
            upload_batch_size=1,
        )
        files = [FileInfo(path="data/test.zip", size=len(archive_bytes), updated=datetime.now())]

        with pytest.raises(RuntimeError, match="Upload failures"):
            expander.expand_archives(files)

    def test_default_batch_size_is_sequential(self, mock_source: MagicMock) -> None:
        """Default batch_size=1 means sequential uploads."""
        archive_bytes = _make_zip({"file.txt": b"data"})
        mock_source._storage["data/test.zip"] = archive_bytes

        expander = ArchiveExpander(
            mock_source,
            expanded_prefix="expanded/",
        )
        assert expander._upload_batch_size == 1
