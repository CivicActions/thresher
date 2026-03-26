"""Controller file scanner — lists and classifies source files."""

from __future__ import annotations

import json
import logging

from thresher.config import Config
from thresher.controller.archive_expander import ArchiveExpander, is_archive
from thresher.processing.classifier import classify_file
from thresher.providers.source import SourceProvider
from thresher.types import FileInfo

logger = logging.getLogger("thresher.controller.scanner")


def _load_skip_list(source: SourceProvider, queue_prefix: str) -> set[str]:
    """Load the skip list from ``{queue_prefix}skip-list.json``."""
    path = f"{queue_prefix}skip-list.json"
    if not source.exists(path):
        return set()
    try:
        data = source.download_content(path)
        paths = json.loads(data.decode("utf-8"))
        return set(paths)
    except Exception:
        logger.warning("Could not load skip list at %s; starting fresh", path)
        return set()


def _save_skip_list(source: SourceProvider, queue_prefix: str, skip_list: set[str]) -> None:
    """Save the skip list to ``{queue_prefix}skip-list.json``."""
    path = f"{queue_prefix}skip-list.json"
    data = json.dumps(sorted(skip_list)).encode("utf-8")
    source.upload_content(path, data)


def update_skip_list(source: SourceProvider, queue_prefix: str, paths: list[str]) -> None:
    """Add *paths* to the persistent skip list."""
    skip_list = _load_skip_list(source, queue_prefix)
    skip_list.update(paths)
    _save_skip_list(source, queue_prefix, skip_list)


def scan_files(
    source: SourceProvider,
    config: Config,
) -> list[dict]:
    """Scan source provider for processable files.

    Returns list of dicts with: path, source_type, file_type_group, file_size.
    Archives are expanded and their members are classified individually.
    """
    prefix = config.source.gcs.source_prefix
    queue_prefix = config.source.gcs.queue_prefix
    items: list[dict] = []
    archives: list[FileInfo] = []
    skipped = 0

    # Load skip list (unless --force)
    skip_set: set[str] = set()
    if not config.force:
        skip_set = _load_skip_list(source, queue_prefix)
        if skip_set:
            logger.info("Loaded skip list with %d entries", len(skip_set))

    logger.info("Scanning files with prefix: %s", prefix or "(root)")

    skip_list_skipped = 0
    for file_info in source.list_files(prefix=prefix, recursive=True):
        # Skip directories
        if file_info.path.endswith("/"):
            continue

        # Collect archives for expansion instead of classifying them
        if is_archive(file_info.path):
            archives.append(file_info)
            continue

        # Skip list check
        if file_info.path in skip_set:
            skip_list_skipped += 1
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
            exclude_extensions=config.processing.archive_exclude_extensions,
        )
        for exp in expander.expand_archives(archives):
            if exp["path"] in skip_set:
                skip_list_skipped += 1
                continue
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

    if skip_list_skipped:
        logger.info("Skip list filtered %d previously-processed files", skip_list_skipped)
    logger.info("Scan complete: %d files queued, %d skipped", len(items), skipped)
    return items


def scan_summary(items: list[dict]) -> dict:
    """Generate a summary of scanned files for dry-run reporting."""
    by_group: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_size = 0

    for item in items:
        group = item.get("file_type_group", "unknown")
        source_type = item.get("source_type", "direct")
        by_group[group] = by_group.get(group, 0) + 1
        by_type[source_type] = by_type.get(source_type, 0) + 1
        total_size += item.get("file_size") or 0

    return {
        "total_files": len(items),
        "by_group": by_group,
        "by_source_type": by_type,
        "total_size_bytes": total_size,
    }
