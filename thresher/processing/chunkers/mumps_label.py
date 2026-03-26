"""MUMPS label-boundary chunker.

Splits MUMPS source code at routine label boundaries, preserving
subroutine-level grouping. Oversized sections are split at blank-line
boundaries within the section.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

logger = logging.getLogger("thresher.chunkers.mumps_label")

# Regex to find MUMPS labels in column 1
_MUMPS_LABEL_RE = re.compile(
    r"^(%?[A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?(?=[\s;(]|$)",
    re.MULTILINE,
)


def chunk_mumps_source(
    text: str,
    chunk_size: int = 512,
    count_tokens: Callable[[str], int] | None = None,
) -> list[dict]:
    """Chunk MUMPS source code at label boundaries.

    Args:
        text: MUMPS source code text
        chunk_size: Maximum tokens per chunk
        count_tokens: Optional function to count tokens. If None, estimates
                      based on character count (chars / 4).

    Returns:
        List of chunk dicts with keys: text, routine_name, is_header,
        line_start (1-based), line_end (1-based)
    """
    if count_tokens is None:

        def count_tokens(t: str) -> int:
            return len(t) // 4

    lines = text.split("\n")

    # Find all label positions
    labels: list[tuple[int, str]] = []  # (line_number_0based, label_name)
    for i, line in enumerate(lines):
        match = _MUMPS_LABEL_RE.match(line)
        if match:
            labels.append((i, match.group(1)))

    # Build sections: header + each labeled routine
    sections: list[tuple[str, int, int, bool]] = []  # (name, start, end, is_header)

    if not labels:
        # No labels found — treat entire file as one section
        sections.append(("_file", 0, len(lines), True))
    else:
        # Header section (before first label)
        if labels[0][0] > 0:
            sections.append(("_header", 0, labels[0][0], True))

        # Label sections
        for idx, (line_num, label_name) in enumerate(labels):
            end = labels[idx + 1][0] if idx + 1 < len(labels) else len(lines)
            sections.append((label_name, line_num, end, False))

    # Chunk each section
    chunks: list[dict] = []
    for name, start, end, is_header in sections:
        section_lines = lines[start:end]
        section_text = "\n".join(section_lines)
        token_count = count_tokens(section_text)

        if token_count <= chunk_size:
            chunks.append(
                {
                    "text": section_text,
                    "routine_name": name,
                    "is_header": is_header,
                    "line_start": start + 1,
                    "line_end": end,
                }
            )
        else:
            # Split oversized section at blank-line boundaries
            sub_chunks = _split_oversized_section(
                section_lines, start, name, is_header, chunk_size, count_tokens
            )
            chunks.extend(sub_chunks)

    return chunks


def _split_oversized_section(
    lines: list[str],
    start_offset: int,
    label_name: str,
    is_header: bool,
    chunk_size: int,
    count_tokens: Callable[[str], int],
) -> list[dict]:
    """Split an oversized section at blank-line boundaries."""
    chunks: list[dict] = []
    current_lines: list[str] = []
    chunk_start = start_offset

    for i, line in enumerate(lines):
        current_lines.append(line)
        current_text = "\n".join(current_lines)

        if count_tokens(current_text) >= chunk_size and current_lines:
            # Emit current chunk
            chunks.append(
                {
                    "text": current_text,
                    "routine_name": label_name,
                    "is_header": is_header,
                    "line_start": chunk_start + 1,
                    "line_end": start_offset + i + 1,
                }
            )
            current_lines = []
            chunk_start = start_offset + i + 1

    # Emit remaining lines
    if current_lines:
        remaining_text = "\n".join(current_lines)
        if remaining_text.strip():
            chunks.append(
                {
                    "text": remaining_text,
                    "routine_name": label_name,
                    "is_header": is_header,
                    "line_start": chunk_start + 1,
                    "line_end": start_offset + len(lines),
                }
            )

    return chunks
