"""Unit tests for ExpansionOrchestrator."""

from __future__ import annotations

import io
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from thresher.config import Config, GCSConfig, ProcessingConfig, SourceConfig
from thresher.controller.expansion_orchestrator import ExpansionOrchestrator
from thresher.types import ExpansionResult, FileInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_config(**overrides) -> Config:
    cfg = Config()
    proc_kwargs = {
        "max_expansion_parallelism": 2,
        "upload_batch_size": 10,
        "expansion_timeout": 60,
    }
    proc_kwargs.update(overrides)
    cfg.processing = ProcessingConfig(**proc_kwargs)
    cfg.source = SourceConfig(
        provider="gcs",
        gcs=GCSConfig(
            bucket="test-bucket",
            source_prefix="source/",
            expanded_prefix="expanded/",
        ),
    )
    return cfg


def _mock_source_with_archives(archives: dict[str, bytes]) -> MagicMock:
    """Create a mock source provider backed by an in-memory dict."""
    storage: dict[str, bytes] = dict(archives)
    source = MagicMock()

    def exists(path: str) -> bool:
        return path in storage

    def download_content(path: str) -> bytes:
        if path not in storage:
            raise FileNotFoundError(path)
        return storage[path]

    def download_to_path(path: str, local_path: Path) -> None:
        data = storage[path]
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)

    def upload_from_path(path: str, local_path: Path) -> None:
        storage[path] = local_path.read_bytes()

    def upload_content(path: str, data: bytes) -> None:
        storage[path] = data

    def list_files(prefix: str = "", recursive: bool = True):
        for p in sorted(storage):
            if p.startswith(prefix):
                yield FileInfo(path=p, size=len(storage[p]), updated=datetime.now())

    source.exists = MagicMock(side_effect=exists)
    source.download_content = MagicMock(side_effect=download_content)
    source.download_to_path = MagicMock(side_effect=download_to_path)
    source.upload_from_path = MagicMock(side_effect=upload_from_path)
    source.upload_content = MagicMock(side_effect=upload_content)
    source.list_files = MagicMock(side_effect=list_files)
    source._storage = storage  # expose for assertions
    return source


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExpandLocal:
    """Tests for local-mode archive expansion."""

    def test_expands_single_archive(self):
        archive_bytes = _make_zip({"readme.txt": b"hello", "data.csv": b"a,b,c"})
        source = _mock_source_with_archives({"source/test.zip": archive_bytes})
        config = _make_config()

        orch = ExpansionOrchestrator(config, source)
        fi = FileInfo(path="source/test.zip", size=len(archive_bytes), updated=datetime.now())
        result = orch.expand_local([fi])

        assert isinstance(result, ExpansionResult)
        assert result.archives_expanded == 1
        assert result.archives_failed == 0
        assert result.files_extracted == 2
        assert result.duration_seconds > 0

    def test_expands_multiple_archives(self):
        zip1 = _make_zip({"a.txt": b"aaa"})
        zip2 = _make_zip({"b.txt": b"bbb", "c.txt": b"ccc"})
        source = _mock_source_with_archives(
            {"source/one.zip": zip1, "source/two.zip": zip2}
        )
        config = _make_config()

        orch = ExpansionOrchestrator(config, source)
        archives = [
            FileInfo(path="source/one.zip", size=len(zip1), updated=datetime.now()),
            FileInfo(path="source/two.zip", size=len(zip2), updated=datetime.now()),
        ]
        result = orch.expand_local(archives)

        assert result.archives_expanded == 2
        assert result.files_extracted == 3
        assert result.failed_archives == []

    def test_handles_failed_archive(self):
        source = _mock_source_with_archives({"source/bad.zip": b"not-a-zip"})
        config = _make_config()

        orch = ExpansionOrchestrator(config, source)
        fi = FileInfo(path="source/bad.zip", size=10, updated=datetime.now())
        result = orch.expand_local([fi])

        assert result.archives_failed == 1
        assert "source/bad.zip" in result.failed_archives

    def test_empty_archive_list(self):
        source = _mock_source_with_archives({})
        config = _make_config()

        orch = ExpansionOrchestrator(config, source)
        result = orch.expand_local([])

        assert result.archives_expanded == 0
        assert result.archives_failed == 0
        assert result.files_extracted == 0


class TestIdempotency:
    """Tests for skipping already-expanded archives."""

    def test_skips_already_expanded(self):
        archive_bytes = _make_zip({"readme.txt": b"hello"})
        record = json.dumps({
            "archive_path": "source/test.zip",
            "expansion_folder": "expanded/source/test",
            "member_count": 1,
            "expanded_at": time.time(),
            "archive_hash": "abc123",
        }).encode()
        source = _mock_source_with_archives({
            "source/test.zip": archive_bytes,
            "expanded/source/test/.expansion-record.json": record,
        })
        config = _make_config()

        orch = ExpansionOrchestrator(config, source)
        fi = FileInfo(path="source/test.zip", size=len(archive_bytes), updated=datetime.now())
        result = orch.expand_local([fi])

        assert result.archives_expanded == 1  # counted as skipped
        assert result.archives_failed == 0
        assert result.files_extracted == 0  # no new extraction

    def test_expands_mix_of_new_and_existing(self):
        zip1 = _make_zip({"a.txt": b"aaa"})
        zip2 = _make_zip({"b.txt": b"bbb"})
        record = json.dumps({
            "archive_path": "source/existing.zip",
            "expansion_folder": "expanded/source/existing",
            "member_count": 1,
            "expanded_at": time.time(),
            "archive_hash": "abc",
        }).encode()
        source = _mock_source_with_archives({
            "source/existing.zip": zip1,
            "source/new.zip": zip2,
            "expanded/source/existing/.expansion-record.json": record,
        })
        config = _make_config()

        orch = ExpansionOrchestrator(config, source)
        archives = [
            FileInfo(path="source/existing.zip", size=len(zip1), updated=datetime.now()),
            FileInfo(path="source/new.zip", size=len(zip2), updated=datetime.now()),
        ]
        result = orch.expand_local(archives)

        assert result.archives_expanded == 2  # 1 skipped + 1 new
        assert result.archives_failed == 0
        assert result.files_extracted == 1  # only the new archive


class TestExpandLocalParallelism:
    """Tests for thread pool configuration."""

    def test_uses_configured_parallelism(self):
        """Verify thread pool uses max_expansion_parallelism."""
        zip_data = _make_zip({"file.txt": b"data"})
        source = _mock_source_with_archives({
            "source/a.zip": zip_data,
            "source/b.zip": zip_data,
            "source/c.zip": zip_data,
        })
        config = _make_config(max_expansion_parallelism=2)

        orch = ExpansionOrchestrator(config, source)
        archives = [
            FileInfo(path=f"source/{n}.zip", size=len(zip_data), updated=datetime.now())
            for n in ["a", "b", "c"]
        ]
        result = orch.expand_local(archives)

        assert result.archives_expanded == 3
        assert result.archives_failed == 0
