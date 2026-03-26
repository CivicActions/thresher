"""Chonkie CodeChunker wrapper for AST-based source code chunking."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("thresher.chunkers.chonkie_code")


# Extension to tree-sitter language mapping
LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".pl": "perl",
    ".r": "r",
    ".sql": "sql",
    ".lua": "lua",
    ".zig": "zig",
    ".ex": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
}


def detect_language(file_path: str, hint: str = "auto") -> str:
    """Detect programming language from file extension or hint."""
    if hint and hint != "auto":
        return hint
    ext = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(ext, "python")


def chunk_code(
    text: str,
    chunk_size: int = 512,
    language: str = "python",
    file_path: str = "",
) -> list[dict[str, Any]]:
    """Chunk source code using Chonkie CodeChunker (tree-sitter AST).

    Args:
        text: Source code text
        chunk_size: Maximum tokens per chunk
        language: Programming language name for tree-sitter
        file_path: Optional file path for context

    Returns:
        List of chunk dicts with keys: text, start_index, end_index,
        token_count, start_line, end_line
    """
    if not text.strip():
        return []

    try:
        from chonkie import CodeChunker
    except ImportError:
        logger.warning("chonkie CodeChunker not available, falling back to line splitting")
        return _fallback_line_chunks(text, chunk_size)

    try:
        chunker = CodeChunker(
            tokenizer="sentence-transformers/all-MiniLM-L6-v2",
            chunk_size=chunk_size,
            language=language,
        )
        chunks = chunker.chunk(text)
    except Exception as e:
        logger.warning("CodeChunker failed for %s: %s, falling back", language, e)
        return _fallback_line_chunks(text, chunk_size)

    results: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_text = chunk.text
        start_line, end_line = _compute_line_numbers(text, chunk_text)

        results.append(
            {
                "text": chunk_text,
                "start_index": chunk.start_index,
                "end_index": chunk.end_index,
                "token_count": chunk.token_count,
                "start_line": start_line,
                "end_line": end_line,
            }
        )

    return results


def _compute_line_numbers(full_text: str, chunk_text: str) -> tuple[int, int]:
    """Compute 1-based start and end line numbers for a chunk within the full text."""
    pos = full_text.find(chunk_text)
    if pos < 0:
        total_lines = full_text.count("\n") + 1
        return (1, total_lines)

    start_line = full_text[:pos].count("\n") + 1
    end_line = start_line + chunk_text.count("\n")
    return (start_line, end_line)


def _fallback_line_chunks(text: str, chunk_size: int) -> list[dict[str, Any]]:
    """Simple line-based fallback when CodeChunker is unavailable."""
    lines = text.split("\n")
    # Rough estimate: 4 chars per token
    chars_per_chunk = chunk_size * 4

    chunks: list[dict[str, Any]] = []
    current_lines: list[str] = []
    current_chars = 0
    start_line = 1
    char_offset = 0

    for i, line in enumerate(lines, 1):
        current_lines.append(line)
        current_chars += len(line) + 1

        if current_chars >= chars_per_chunk:
            chunk_text = "\n".join(current_lines)
            chunks.append(
                {
                    "text": chunk_text,
                    "start_index": char_offset,
                    "end_index": char_offset + len(chunk_text),
                    "token_count": len(chunk_text) // 4,
                    "start_line": start_line,
                    "end_line": i,
                }
            )
            char_offset += len(chunk_text) + 1
            current_lines = []
            current_chars = 0
            start_line = i + 1

    if current_lines:
        chunk_text = "\n".join(current_lines)
        chunks.append(
            {
                "text": chunk_text,
                "start_index": char_offset,
                "end_index": char_offset + len(chunk_text),
                "token_count": len(chunk_text) // 4,
                "start_line": start_line,
                "end_line": len(lines),
            }
        )

    return chunks
