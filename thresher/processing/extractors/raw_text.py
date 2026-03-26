"""Raw text extraction — reads file bytes and decodes as text."""

from __future__ import annotations

import logging

logger = logging.getLogger("thresher.extractors.raw_text")


def extract_raw_text(content: bytes) -> str | None:
    """Extract text from raw bytes, trying common encodings.

    Returns None if content cannot be decoded as text.
    """
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    return None
