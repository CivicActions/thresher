"""Chonkie RecursiveChunker wrapper for plain text and markdown."""

from __future__ import annotations

import logging

logger = logging.getLogger("thresher.chunkers.chonkie_recursive")


def chunk_with_recursive(
    text: str,
    chunk_size: int = 512,
    recipe: str = "",
    tokenizer: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> list[dict]:
    """Chunk text using Chonkie's RecursiveChunker.

    Args:
        text: Text to chunk
        chunk_size: Maximum tokens per chunk
        recipe: Splitting recipe (e.g., "markdown"). Empty for default rules.
        tokenizer: Tokenizer model name for token-accurate chunking

    Returns:
        List of chunk dicts with keys: text, start_index, end_index, token_count
    """
    if not text.strip():
        return []

    try:
        from chonkie import RecursiveChunker
    except ImportError:
        logger.warning("chonkie not available, falling back to simple splitting")
        return _simple_split(text, chunk_size)

    if recipe:
        chunker = RecursiveChunker.from_recipe(
            recipe,
            tokenizer=tokenizer,
            chunk_size=chunk_size,
            min_characters_per_chunk=24,
        )
    else:
        chunker = RecursiveChunker(
            tokenizer=tokenizer,
            chunk_size=chunk_size,
            min_characters_per_chunk=24,
        )

    result_chunks = chunker.chunk(text)

    chunks = []
    for chunk in result_chunks:
        chunks.append(
            {
                "text": chunk.text,
                "start_index": chunk.start_index,
                "end_index": chunk.end_index,
                "token_count": chunk.token_count,
            }
        )

    return chunks


def _simple_split(text: str, chunk_size: int) -> list[dict]:
    """Fallback simple text splitting by approximate token count."""
    # Rough estimate: 4 chars per token
    chars_per_chunk = chunk_size * 4
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chars_per_chunk, len(text))
        # Try to break at a newline
        if end < len(text):
            newline_pos = text.rfind("\n", start, end)
            if newline_pos > start:
                end = newline_pos + 1
        chunk_text = text[start:end]
        if chunk_text.strip():
            chunks.append(
                {
                    "text": chunk_text,
                    "start_index": start,
                    "end_index": end,
                    "token_count": len(chunk_text) // 4,
                }
            )
        start = end
    return chunks
