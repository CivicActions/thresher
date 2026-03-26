"""File type classification based on extension, MIME type, and content detectors."""

from __future__ import annotations

import logging
import os

from thresher.types import FileTypeGroup

logger = logging.getLogger("thresher.classifier")


def classify_file(
    file_path: str,
    file_type_groups: dict[str, FileTypeGroup],
    content: bytes | None = None,
) -> str | None:
    """Classify a file into a file type group.

    Checks groups in priority order (lower = checked first).
    Returns group name or None if no match (binary/skip).
    """
    ext = os.path.splitext(file_path)[1].lower()

    # Sort groups by priority
    sorted_groups = sorted(file_type_groups.values(), key=lambda g: g.priority)

    for group in sorted_groups:
        # Skip the binary/skip group for now (it's a catch-all)
        if group.extractor == "skip":
            continue

        # Check extension match
        if ext and ext in group.extensions:
            return group.name

        # Check MIME type match (prefix matching)
        if content is not None:
            mime = _detect_mime_type(content, file_path)
            if mime:
                for mime_prefix in group.mime_types:
                    if mime.startswith(mime_prefix):
                        return group.name

    # No match — check if it's binary
    if content is not None and _is_binary(content):
        return None

    return None


def _detect_mime_type(content: bytes, file_path: str) -> str | None:
    """Detect MIME type using python-magic."""
    try:
        import magic

        return magic.from_buffer(content[:4096], mime=True)
    except Exception:
        return None


def _is_binary(content: bytes) -> bool:
    """Check if content appears to be binary (contains null bytes)."""
    # Check first 8KB for null bytes
    sample = content[:8192]
    return b"\x00" in sample
