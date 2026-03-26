"""File type classification based on extension, MIME type, and content detectors."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable

from thresher.types import FileTypeGroup

logger = logging.getLogger("thresher.classifier")

# Regex for MUMPS labels: line-start identifier optionally followed by formal params
_MUMPS_LABEL_RE = re.compile(
    rb"^(%?[A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?(?=[\s;(]|$)", re.MULTILINE
)

# Minimum number of MUMPS label matches to consider a file MUMPS source
_MUMPS_LABEL_MIN_MATCHES = 3

# Minimum caret density to flag as MUMPS globals
_CARET_DENSITY_THRESHOLD = 0.05


def _detect_mumps_labels(content: bytes, file_path: str) -> bool:
    """Detect MUMPS label patterns at the start of lines."""
    matches = _MUMPS_LABEL_RE.findall(content[:65536])
    return len(matches) >= _MUMPS_LABEL_MIN_MATCHES


def _detect_caret_density(content: bytes, file_path: str) -> bool:
    """Detect high caret (^) density indicating MUMPS globals."""
    sample = content[:65536]
    if not sample:
        return False
    caret_count = sample.count(ord("^")) if isinstance(sample, (bytes, bytearray)) else 0
    return (caret_count / len(sample)) > _CARET_DENSITY_THRESHOLD


DETECTORS: dict[str, Callable[[bytes, str], bool]] = {
    "mumps-labels": _detect_mumps_labels,
    "caret-density": _detect_caret_density,
}

# Extensions recognized as image types for size-threshold checks
_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".svg", ".webp", ".ico"]
)


def classify_file(
    file_path: str,
    file_type_groups: dict[str, FileTypeGroup],
    content: bytes | None = None,
) -> str | None:
    """Classify a file into a file type group.

    Checks groups in priority order (lower = checked first).
    For each group, matches on extension, MIME type, or content detectors.
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

        # Check content detectors
        if content is not None and group.detectors:
            for detector_name in group.detectors:
                detector_fn = DETECTORS.get(detector_name)
                if detector_fn and detector_fn(content, file_path):
                    return group.name

    # No match — check if it's binary
    if content is not None and _is_binary(content):
        return None

    return None


def should_skip_image(file_path: str, file_size: int | None, min_size: int) -> bool:
    """Check if an image file should be skipped due to being below the minimum size.

    Args:
        file_path: Path to the file.
        file_size: Size of the file in bytes, or None if unknown.
        min_size: Minimum size in bytes; images smaller are skipped.

    Returns:
        True if the file is an image below the minimum size threshold.
    """
    if file_size is None:
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _IMAGE_EXTENSIONS:
        return False
    return file_size < min_size


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
