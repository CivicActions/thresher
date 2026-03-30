"""Pipeline status reporting — queries GCS queue and Qdrant collections."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from thresher.config import Config
from thresher.providers.source import SourceProvider

logger = logging.getLogger("thresher.controller.status")


@dataclass
class QueueStatus:
    """Snapshot of the queue state."""

    pending: int = 0
    claimed: int = 0
    done: int = 0
    retry: int = 0
    total: int = 0
    skip_list_size: int = 0
    oldest_done_ts: float | None = None
    newest_done_ts: float | None = None


@dataclass
class CollectionStatus:
    """Snapshot of a single Qdrant collection."""

    name: str
    points_count: int = 0
    status: str = "unknown"


@dataclass
class PipelineStatus:
    """Full pipeline status snapshot."""

    queue: QueueStatus = field(default_factory=QueueStatus)
    collections: list[CollectionStatus] = field(default_factory=list)
    batch_size: int = 250


def get_queue_status(source: SourceProvider, queue_prefix: str) -> QueueStatus:
    """Count batches in each queue state."""
    status = QueueStatus()

    for prefix_name, attr in [
        ("pending/", "pending"),
        ("done/", "done"),
        ("retry/", "retry"),
    ]:
        count = 0
        for fi in source.list_files(prefix=f"{queue_prefix}{prefix_name}"):
            if fi.path.endswith(".json"):
                count += 1
                # Track done timestamps for ETA calculation
                if prefix_name == "done/" and fi.updated:
                    ts = fi.updated.timestamp()
                    if status.oldest_done_ts is None or ts < status.oldest_done_ts:
                        status.oldest_done_ts = ts
                    if status.newest_done_ts is None or ts > status.newest_done_ts:
                        status.newest_done_ts = ts
        setattr(status, attr, count)

    # Count claimed batches (nested under runner-id dirs)
    claimed = 0
    for fi in source.list_files(prefix=f"{queue_prefix}claimed/", recursive=True):
        if fi.path.endswith(".json"):
            claimed += 1
    status.claimed = claimed

    status.total = status.pending + status.claimed + status.done + status.retry

    # Skip list
    skip_path = f"{queue_prefix}skip-list.json"
    if source.exists(skip_path):
        try:
            data = source.download_content(skip_path)
            status.skip_list_size = len(json.loads(data.decode("utf-8")))
        except Exception:
            pass

    return status


def get_collection_statuses(config: Config) -> list[CollectionStatus]:
    """Query Qdrant for collection point counts."""
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        logger.warning("qdrant-client not installed, skipping collection stats")
        return []

    try:
        url = config.destination.qdrant.url
        # qdrant-client defaults to port 6333 when no port is specified;
        # for HTTPS URLs without an explicit port, append :443.
        if url.startswith("https://") and url.count(":") == 1:
            url = url + ":443"

        client = QdrantClient(
            url=url,
            api_key=config.destination.qdrant.api_key or None,
            timeout=config.destination.qdrant.timeout,
            prefer_grpc=False,
        )
    except Exception as e:
        logger.warning("Could not connect to Qdrant: %s", e)
        return []

    statuses: list[CollectionStatus] = []
    try:
        collections = client.get_collections().collections
        for col in collections:
            try:
                info = client.get_collection(col.name)
                statuses.append(
                    CollectionStatus(
                        name=col.name,
                        points_count=info.points_count or 0,
                        status=str(info.status),
                    )
                )
            except Exception as e:
                logger.debug("Error reading collection %s: %s", col.name, e)
                statuses.append(CollectionStatus(name=col.name))
    except Exception as e:
        logger.warning("Could not list Qdrant collections: %s", e)
    finally:
        client.close()

    return sorted(statuses, key=lambda c: c.name)


def get_pipeline_status(source: SourceProvider, config: Config) -> PipelineStatus:
    """Gather full pipeline status."""
    queue = get_queue_status(source, config.source.gcs.queue_prefix)
    collections = get_collection_statuses(config)
    return PipelineStatus(
        queue=queue,
        collections=collections,
        batch_size=config.queue.batch_size,
    )


def format_status(status: PipelineStatus) -> str:
    """Format pipeline status as a human-readable string."""
    lines: list[str] = []

    q = status.queue
    pct = (q.done * 100 // q.total) if q.total > 0 else 0
    files_done = q.done * status.batch_size
    files_total = q.total * status.batch_size

    lines.append("=== Queue Progress ===")
    lines.append(f"  Done:    {q.done:,} batches ({pct}%)")
    lines.append(f"  Claimed: {q.claimed:,} batches (in progress)")
    lines.append(f"  Pending: {q.pending:,} batches")
    if q.retry:
        lines.append(f"  Retry:   {q.retry:,} batches")
    lines.append(f"  Total:   {q.total:,} batches (~{files_total:,} files)")
    lines.append(f"  Files processed: ~{files_done:,} / ~{files_total:,}")

    if q.skip_list_size:
        lines.append(f"  Skip list: {q.skip_list_size:,} entries")

    # ETA calculation
    if q.oldest_done_ts and q.newest_done_ts and q.done > 1:
        elapsed = q.newest_done_ts - q.oldest_done_ts
        if elapsed > 0:
            rate = q.done / (elapsed / 3600)  # batches per hour
            remaining = q.pending + q.claimed
            if rate > 0:
                eta_hours = remaining / rate
                lines.append(f"  Throughput: {rate:.0f} batches/hr")
                if eta_hours < 1:
                    lines.append(f"  ETA: {eta_hours * 60:.0f} minutes")
                else:
                    lines.append(f"  ETA: {eta_hours:.1f} hours")

    if status.collections:
        lines.append("")
        lines.append("=== Qdrant Collections ===")
        total_points = 0
        for col in status.collections:
            lines.append(f"  {col.name}: {col.points_count:,} points ({col.status})")
            total_points += col.points_count
        lines.append(f"  Total: {total_points:,} points")

    return "\n".join(lines)
