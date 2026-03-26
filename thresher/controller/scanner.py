"""Controller file scanner — lists and classifies source files."""

from __future__ import annotations

import logging

from thresher.config import Config
from thresher.controller.archive_expander import ArchiveExpander, is_archive
from thresher.processing.classifier import classify_file
from thresher.providers.source import SourceProvider
from thresher.types import FileInfo

logger = logging.getLogger("thresher.controller.scanner")


def scan_files(
    source: SourceProvider,
    config: Config,
) -> list[dict]:
    """Scan source provider for processable files.

    Returns list of dicts with: path, source_type, file_type_group, file_size.
    Archives are expanded and their members are classified individually.
    """
    prefix = config.source.gcs.source_prefix
    items: list[dict] = []
    archives: list[FileInfo] = []
    skipped = 0

    logger.info("Scanning files with prefix: %s", prefix or "(root)")

    for file_info in source.list_files(prefix=prefix, recursive=True):
        # Skip directories
        if file_info.path.endswith("/"):
            continue

        # Collect archives for expansion instead of classifying them
        if is_archive(file_info.path):
            archives.append(file_info)
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

    # Expand archives and classify the expanded files
    if archives:
        logger.info("Expanding %d archive(s)", len(archives))
        expander = ArchiveExpander(
            source=source,
            expanded_prefix=config.source.gcs.expanded_prefix,
            max_depth=config.processing.archive_depth,
        )
        for exp in expander.expand_archives(archives):
            group = classify_file(exp["path"], config.file_type_groups)
            if group is None:
                skipped += 1
                continue
            items.append(
                {
                    "path": exp["path"],
                    "source_type": "expanded",
                    "file_type_group": group,
                    "file_size": None,
                    "archive_path": exp["archive_path"],
                }
            )

    logger.info("Scan complete: %d files queued, %d skipped", len(items), skipped)
    return items
