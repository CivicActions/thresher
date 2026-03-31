"""Queue builder — partitions files into batch JSON files on the source provider."""

from __future__ import annotations

import json
import logging
import time

from thresher.providers.source import SourceProvider
from thresher.types import QueueBatch, QueueItem

logger = logging.getLogger("thresher.controller.queue_builder")


def build_queue(
    items: list[dict],
    source: SourceProvider,
    queue_prefix: str = "queue/",
    batch_size: int = 1000,
) -> list[str]:
    """Partition items into queue batch files on the source provider.

    Args:
        items: List of file dicts from scanner (path, source_type, file_type_group, file_size)
        source: Source provider for writing batch files
        queue_prefix: Prefix for queue paths
        batch_size: Maximum items per batch

    Returns:
        List of batch IDs created
    """
    if not items:
        logger.info("No items to queue")
        return []

    batch_ids: list[str] = []
    created_at = time.time()

    for i in range(0, len(items), batch_size):
        batch_num = (i // batch_size) + 1
        batch_id = f"batch-{batch_num:04d}"
        batch_items = items[i : i + batch_size]

        queue_items = [
            QueueItem(
                path=item["path"],
                source_type=item["source_type"],
                file_type_group=item.get("file_type_group"),
                file_size=item.get("file_size"),
            )
            for item in batch_items
        ]

        batch = QueueBatch(
            batch_id=batch_id,
            created_at=created_at,
            item_count=len(queue_items),
            items=queue_items,
        )

        # Serialize and write to pending queue
        batch_path = f"{queue_prefix}pending/{batch_id}.json"
        batch_data = _serialize_batch(batch)
        source.upload_content(batch_path, batch_data.encode("utf-8"))

        batch_ids.append(batch_id)
        logger.info("Created batch %s with %d items", batch_id, len(queue_items))

    logger.info("Queue built: %d batches, %d total items", len(batch_ids), len(items))
    return batch_ids


def queue_summary(batch_ids: list[str], items: list[dict]) -> dict:
    """Generate a summary of queue building results."""
    return {
        "total_files": len(items),
        "batches_created": len(batch_ids),
        "batch_ids": batch_ids,
    }


def _serialize_batch(batch: QueueBatch) -> str:
    """Serialize a QueueBatch to JSON."""
    data: dict = {
        "batch_id": batch.batch_id,
        "created_at": batch.created_at,
        "item_count": batch.item_count,
        "items": [
            {
                "path": item.path,
                "source_type": item.source_type,
                "status": item.status,
                "attempt_count": item.attempt_count,
                "archive_path": item.archive_path,
                "file_type_group": item.file_type_group,
                "file_size": item.file_size,
                "last_error": item.last_error,
                "completed_at": item.completed_at,
            }
            for item in batch.items
        ],
    }
    if batch.claimed_at is not None:
        data["claimed_at"] = batch.claimed_at
    if batch.runner_id is not None:
        data["runner_id"] = batch.runner_id
    if batch.reclaim_count:
        data["reclaim_count"] = batch.reclaim_count
    return json.dumps(data, indent=2)


def deserialize_batch(data: str) -> QueueBatch:
    """Deserialize JSON string to QueueBatch."""
    raw = json.loads(data)
    items = [
        QueueItem(
            path=item["path"],
            source_type=item["source_type"],
            status=item.get("status", "pending"),
            attempt_count=item.get("attempt_count", 0),
            archive_path=item.get("archive_path"),
            file_type_group=item.get("file_type_group"),
            file_size=item.get("file_size"),
            last_error=item.get("last_error"),
            completed_at=item.get("completed_at"),
        )
        for item in raw["items"]
    ]
    return QueueBatch(
        batch_id=raw["batch_id"],
        created_at=raw["created_at"],
        item_count=raw["item_count"],
        items=items,
        claimed_at=raw.get("claimed_at"),
        runner_id=raw.get("runner_id"),
        reclaim_count=raw.get("reclaim_count", 0),
    )
