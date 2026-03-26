from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# Fixed UUID5 namespace for deterministic point IDs
THRESHER_UUID_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def make_point_id(source_path: str, chunk_index: int) -> str:
    """Deterministic UUID5 from source path and chunk index."""
    return str(uuid.uuid5(THRESHER_UUID_NAMESPACE, f"{source_path}:{chunk_index}"))


@dataclass
class FileInfo:
    """Metadata about a file on the source provider."""

    path: str
    size: int
    updated: datetime
    content_type: str | None = None


@dataclass
class ChunkerConfig:
    strategy: str
    chunk_size: int = 512
    language: str = "auto"
    recipe: str = ""


@dataclass
class FileTypeGroup:
    name: str
    extensions: list[str] = field(default_factory=list)
    mime_types: list[str] = field(default_factory=list)
    detectors: list[str] = field(default_factory=list)
    priority: int = 100
    extractor: str = "raw-text"
    chunker: ChunkerConfig = field(
        default_factory=lambda: ChunkerConfig(strategy="chonkie-recursive")
    )
    max_file_size: int = 0  # 0 = no limit


@dataclass
class RoutingRule:
    collection: str
    name: str = ""
    file_group: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    filename: list[str] = field(default_factory=list)


@dataclass
class QueueItem:
    path: str
    source_type: str  # "direct" or "expanded"
    status: str = "pending"
    attempt_count: int = 0
    archive_path: str | None = None
    file_type_group: str | None = None
    file_size: int | None = None
    last_error: str | None = None
    completed_at: float | None = None


@dataclass
class QueueBatch:
    batch_id: str
    created_at: float
    item_count: int
    items: list[QueueItem]
    claimed_at: float | None = None
    runner_id: str | None = None


class ProcessingStatus(str, Enum):
    INDEXED = "indexed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class ProcessingResult:
    path: str
    status: ProcessingStatus
    duration_seconds: float
    collection: str | None = None
    chunk_count: int | None = None
    error_message: str | None = None
    content_hash: str | None = None
    file_type_group: str | None = None


@dataclass
class ExpansionRecord:
    archive_path: str
    expansion_folder: str
    member_count: int
    expanded_at: float
    archive_hash: str | None = None


@dataclass
class IndexChunk:
    """A single chunk ready for indexing with its embedding."""

    point_id: str
    text: str
    vector: list[float]
    payload: dict
