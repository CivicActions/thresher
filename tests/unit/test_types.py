from thresher.types import (
    ChunkerConfig,
    FileTypeGroup,
    ProcessingResult,
    ProcessingStatus,
    QueueBatch,
    QueueItem,
    make_point_id,
)


class TestMakePointId:
    def test_deterministic(self):
        """Same inputs always produce the same ID."""
        id1 = make_point_id("docs/readme.md", 0)
        id2 = make_point_id("docs/readme.md", 0)
        assert id1 == id2

    def test_unique_for_different_path(self):
        id1 = make_point_id("docs/readme.md", 0)
        id2 = make_point_id("docs/other.md", 0)
        assert id1 != id2

    def test_unique_for_different_index(self):
        id1 = make_point_id("docs/readme.md", 0)
        id2 = make_point_id("docs/readme.md", 1)
        assert id1 != id2

    def test_returns_valid_uuid_string(self):
        import uuid

        result = make_point_id("file.txt", 42)
        parsed = uuid.UUID(result)
        assert parsed.version == 5


class TestQueueItem:
    def test_defaults(self):
        item = QueueItem(path="data/file.csv", source_type="direct")
        assert item.path == "data/file.csv"
        assert item.source_type == "direct"
        assert item.status == "pending"
        assert item.attempt_count == 0
        assert item.archive_path is None
        assert item.file_type_group is None
        assert item.file_size is None
        assert item.last_error is None
        assert item.completed_at is None

    def test_custom_values(self):
        item = QueueItem(
            path="archive/inner.txt",
            source_type="expanded",
            status="completed",
            attempt_count=2,
            archive_path="archive.zip",
            file_type_group="text",
            file_size=1024,
        )
        assert item.source_type == "expanded"
        assert item.status == "completed"
        assert item.attempt_count == 2
        assert item.archive_path == "archive.zip"
        assert item.file_size == 1024


class TestQueueBatch:
    def test_creation(self):
        items = [
            QueueItem(path="a.txt", source_type="direct"),
            QueueItem(path="b.txt", source_type="direct"),
        ]
        batch = QueueBatch(
            batch_id="batch-001",
            created_at=1700000000.0,
            item_count=2,
            items=items,
        )
        assert batch.batch_id == "batch-001"
        assert batch.item_count == 2
        assert len(batch.items) == 2
        assert batch.claimed_at is None
        assert batch.runner_id is None

    def test_with_runner(self):
        batch = QueueBatch(
            batch_id="batch-002",
            created_at=1700000000.0,
            item_count=0,
            items=[],
            claimed_at=1700000001.0,
            runner_id="runner-abc",
        )
        assert batch.claimed_at == 1700000001.0
        assert batch.runner_id == "runner-abc"


class TestProcessingResult:
    def test_indexed_result(self):
        result = ProcessingResult(
            path="doc.pdf",
            status=ProcessingStatus.INDEXED,
            duration_seconds=1.5,
            collection="documents",
            chunk_count=10,
            content_hash="abc123",
            file_type_group="pdf",
        )
        assert result.status == ProcessingStatus.INDEXED
        assert result.status.value == "indexed"
        assert result.chunk_count == 10

    def test_failed_result(self):
        result = ProcessingResult(
            path="bad.bin",
            status=ProcessingStatus.FAILED,
            duration_seconds=0.1,
            error_message="Unsupported format",
        )
        assert result.status == ProcessingStatus.FAILED
        assert result.error_message == "Unsupported format"
        assert result.collection is None
        assert result.chunk_count is None

    def test_skipped_result(self):
        result = ProcessingResult(
            path="cached.txt",
            status=ProcessingStatus.SKIPPED,
            duration_seconds=0.01,
        )
        assert result.status == ProcessingStatus.SKIPPED


class TestFileTypeGroup:
    def test_defaults(self):
        group = FileTypeGroup(name="text")
        assert group.name == "text"
        assert group.extensions == []
        assert group.mime_types == []
        assert group.detectors == []
        assert group.priority == 100
        assert group.extractor == "raw-text"
        assert isinstance(group.chunker, ChunkerConfig)
        assert group.chunker.strategy == "chonkie-recursive"
        assert group.chunker.chunk_size == 512

    def test_custom_config(self):
        group = FileTypeGroup(
            name="code",
            extensions=[".py", ".js"],
            priority=50,
            extractor="code-parser",
            chunker=ChunkerConfig(strategy="ast", chunk_size=256, language="python"),
        )
        assert group.extensions == [".py", ".js"]
        assert group.priority == 50
        assert group.chunker.strategy == "ast"
        assert group.chunker.language == "python"

    def test_no_mutable_default_sharing(self):
        """Ensure mutable defaults are not shared between instances."""
        g1 = FileTypeGroup(name="a")
        g2 = FileTypeGroup(name="b")
        g1.extensions.append(".txt")
        assert g2.extensions == []
