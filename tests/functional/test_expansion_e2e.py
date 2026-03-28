"""Functional tests for parallel archive expansion using fake-gcs-server."""

from __future__ import annotations

import io
import json
import os
import zipfile

import pytest

from tests.functional.conftest import FAKE_GCS_URL, GCS_BUCKET
from thresher.config import Config, GCSConfig, ProcessingConfig, QueueConfig, SourceConfig
from thresher.controller.expansion_orchestrator import ExpansionOrchestrator
from thresher.controller.queue_builder import build_queue
from thresher.controller.scanner import scan_direct_files, scan_expanded_files
from thresher.types import ChunkerConfig, FileTypeGroup

pytestmark = pytest.mark.functional


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


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


@pytest.fixture
def expansion_config():
    """Config for expansion tests."""
    cfg = Config()
    cfg.source = SourceConfig(
        provider="gcs",
        gcs=GCSConfig(
            bucket=GCS_BUCKET,
            source_prefix="source/",
            expanded_prefix="expanded/",
            queue_prefix="queue/",
        ),
    )
    cfg.processing = ProcessingConfig(
        max_expansion_parallelism=3,
        upload_batch_size=10,
        expansion_timeout=120,
        archive_depth=2,
    )
    cfg.queue = QueueConfig(batch_size=100)
    cfg.file_type_groups = {
        "text": FileTypeGroup(
            name="text",
            extensions=[".txt", ".csv", ".md"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="chonkie-recursive"),
        ),
        "data": FileTypeGroup(
            name="data",
            extensions=[".json", ".xml"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="chonkie-recursive"),
        ),
    }
    return cfg


class TestParallelExpansionE2E:
    """End-to-end tests for parallel archive expansion with fake GCS."""

    def test_expand_multiple_archives_locally(self, gcs_provider, expansion_config):
        """Multiple archives should expand in parallel and all files be accessible."""
        zip1 = _make_zip({"readme.txt": b"Hello from archive 1"})
        zip2 = _make_zip({"data.csv": b"a,b,c\n1,2,3", "notes.txt": b"notes"})
        zip3 = _make_zip({"config.json": b'{"key": "value"}'})

        gcs_provider.upload_content("source/archive1.zip", zip1)
        gcs_provider.upload_content("source/archive2.zip", zip2)
        gcs_provider.upload_content("source/archive3.zip", zip3)
        gcs_provider.upload_content("source/direct.txt", b"direct file content")

        # Phase 1: Scan direct files
        items, archives = scan_direct_files(gcs_provider, expansion_config)

        assert len(items) == 1  # direct.txt
        assert len(archives) == 3

        # Phase 2: Expand archives in parallel
        orch = ExpansionOrchestrator(expansion_config, gcs_provider)
        result = orch.expand_local(archives)

        assert result.archives_expanded == 3
        assert result.archives_failed == 0
        assert result.files_extracted == 4  # 1 + 2 + 1

        # Phase 3: Scan expanded files
        expanded_items = scan_expanded_files(gcs_provider, expansion_config)
        assert len(expanded_items) >= 4

        # Phase 4: Merge and build queue
        all_items = items + expanded_items
        assert len(all_items) >= 5

        batch_ids = build_queue(all_items, gcs_provider, queue_prefix="queue/", batch_size=100)
        assert len(batch_ids) >= 1

    def test_expansion_records_written(self, gcs_provider, expansion_config):
        """Each expanded archive should have an expansion record."""
        zip1 = _make_zip({"file.txt": b"content"})
        gcs_provider.upload_content("source/test.zip", zip1)

        items, archives = scan_direct_files(gcs_provider, expansion_config)
        orch = ExpansionOrchestrator(expansion_config, gcs_provider)
        result = orch.expand_local(archives)

        assert result.archives_expanded == 1

        record_path = "expanded/source/test/.expansion-record.json"
        assert gcs_provider.exists(record_path)

        record_data = json.loads(gcs_provider.download_content(record_path))
        assert record_data["archive_path"] == "source/test.zip"
        assert record_data["member_count"] == 1

    def test_idempotent_expansion(self, gcs_provider, expansion_config):
        """Running expansion twice should skip already-expanded archives."""
        zip1 = _make_zip({"file.txt": b"content"})
        gcs_provider.upload_content("source/test.zip", zip1)

        items, archives = scan_direct_files(gcs_provider, expansion_config)

        orch = ExpansionOrchestrator(expansion_config, gcs_provider)
        result1 = orch.expand_local(archives)
        assert result1.archives_expanded == 1
        assert result1.files_extracted == 1

        result2 = orch.expand_local(archives)
        assert result2.archives_expanded == 1  # counted as skipped
        assert result2.files_extracted == 0

    def test_failed_archive_doesnt_block_others(self, gcs_provider, expansion_config):
        """A corrupt archive should fail without blocking other archives."""
        good_zip = _make_zip({"file.txt": b"good content"})
        gcs_provider.upload_content("source/good.zip", good_zip)
        gcs_provider.upload_content("source/bad.zip", b"not-a-zip-file")

        items, archives = scan_direct_files(gcs_provider, expansion_config)
        assert len(archives) == 2

        orch = ExpansionOrchestrator(expansion_config, gcs_provider)
        result = orch.expand_local(archives)

        assert result.archives_expanded == 1
        assert result.archives_failed == 1
        assert len(result.failed_archives) == 1
        assert "bad.zip" in result.failed_archives[0]

    def test_nested_archive_expansion(self, gcs_provider, expansion_config):
        """Archives containing archives should be recursively expanded."""
        inner_zip = _make_zip({"inner.txt": b"nested content"})
        outer_zip = _make_zip({"inner.zip": inner_zip, "outer.txt": b"outer content"})
        gcs_provider.upload_content("source/nested.zip", outer_zip)

        items, archives = scan_direct_files(gcs_provider, expansion_config)
        orch = ExpansionOrchestrator(expansion_config, gcs_provider)
        result = orch.expand_local(archives)

        assert result.archives_expanded == 1
        assert result.files_extracted >= 2

    def test_concurrent_uploads_work(self, gcs_provider, expansion_config):
        """Large archive with many files should use concurrent uploads."""
        files = {f"file_{i:03d}.txt": f"content {i}".encode() for i in range(50)}
        big_zip = _make_zip(files)
        gcs_provider.upload_content("source/many_files.zip", big_zip)

        items, archives = scan_direct_files(gcs_provider, expansion_config)
        orch = ExpansionOrchestrator(expansion_config, gcs_provider)
        result = orch.expand_local(archives)

        assert result.archives_expanded == 1
        assert result.files_extracted == 50

        expanded = scan_expanded_files(gcs_provider, expansion_config)
        assert len(expanded) == 50
