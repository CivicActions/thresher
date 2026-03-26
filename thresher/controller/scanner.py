"""Controller file scanner — lists and classifies source files."""

from __future__ import annotations

import logging

from thresher.config import Config
from thresher.processing.classifier import classify_file
from thresher.providers.source import SourceProvider

logger = logging.getLogger("thresher.controller.scanner")


def scan_files(
    source: SourceProvider,
    config: Config,
) -> list[dict]:
    """Scan source provider for processable files.

    Returns list of dicts with: path, source_type, file_type_group, file_size
    """
    prefix = config.source.gcs.source_prefix
    items: list[dict] = []
    skipped = 0

    logger.info("Scanning files with prefix: %s", prefix or "(root)")

    for file_info in source.list_files(prefix=prefix, recursive=True):
        # Skip directories
        if file_info.path.endswith("/"):
            continue

        # Classify without content (extension-only for controller)
        group = classify_file(file_info.path, config.file_type_groups)

        if group is None:
            skipped += 1
            continue

        items.append(
            {
                "path": file_info.path,
                "source_type": "direct",
                "file_type_group": group,
                "file_size": file_info.size,
            }
        )

    logger.info("Scan complete: %d files queued, %d skipped", len(items), skipped)
    return items
