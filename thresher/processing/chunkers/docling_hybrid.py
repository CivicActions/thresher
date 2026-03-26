"""Docling HybridChunker wrapper for document chunking."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("thresher.chunkers.docling_hybrid")


def chunk_with_docling_hybrid(
    document_json: str,
    chunk_size: int = 512,
) -> list[dict[str, Any]]:
    """Chunk a docling-extracted document using HybridChunker.

    Args:
        document_json: Serialized DoclingDocument JSON string
        chunk_size: Maximum tokens per chunk

    Returns:
        List of chunk dicts with keys: text, headings
    """
    try:
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
        from docling_core.types.doc import DoclingDocument
    except ImportError:
        logger.warning("docling_core not available, falling back to text splitting")
        return []

    try:
        doc = DoclingDocument.model_validate_json(document_json)
    except Exception:
        logger.warning("Failed to parse DoclingDocument JSON, returning empty chunks")
        return []

    chunker = HybridChunker(
        tokenizer="sentence-transformers/all-MiniLM-L6-v2",  # ty: ignore[invalid-argument-type]
        max_tokens=chunk_size,  # ty: ignore[unknown-argument]
        merge_peers=True,
    )

    chunks: list[dict[str, Any]] = []
    for chunk in chunker.chunk(doc):
        headings: list[str] = []
        if hasattr(chunk, "meta") and chunk.meta:
            headings = [h.text for h in getattr(chunk.meta, "headings", []) if hasattr(h, "text")]
        chunks.append(
            {
                "text": chunk.text,
                "headings": headings,
            }
        )

    return chunks
