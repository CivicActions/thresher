"""Single-file processing pipeline: classify -> extract -> chunk -> embed -> index."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from thresher.config import Config
from thresher.embedder import Embedder
from thresher.processing.classifier import classify_file
from thresher.processing.router import Router
from thresher.providers.destination import DestinationProvider
from thresher.providers.source import SourceProvider
from thresher.types import (
    FileTypeGroup,
    IndexChunk,
    ProcessingResult,
    ProcessingStatus,
    make_point_id,
)
from thresher.url_resolver import resolve_source_url

logger = logging.getLogger("thresher.runner.processor")


class FileProcessor:
    """Processes a single file through the full pipeline."""

    def __init__(
        self,
        source: SourceProvider,
        destination: DestinationProvider,
        embedder: Embedder,
        router: Router,
        config: Config,
    ):
        self.source = source
        self.destination = destination
        self.embedder = embedder
        self.router = router
        self.config = config
        # Determine source-code group names for routing
        self._source_groups = _get_source_group_names(config.file_type_groups)

    def process_file(self, file_path: str, file_type_group: str | None = None) -> ProcessingResult:
        """Process a single file through classify -> extract -> chunk -> embed -> index."""
        start = time.time()

        try:
            # 1. Download content
            content = self.source.download_content(file_path)

            # 2. Classify (re-classify with content if not already classified)
            group_name = file_type_group or classify_file(
                file_path, self.config.file_type_groups, content
            )
            if group_name is None:
                return ProcessingResult(
                    path=file_path,
                    status=ProcessingStatus.SKIPPED,
                    duration_seconds=time.time() - start,
                    file_type_group=group_name,
                )

            group = self.config.file_type_groups.get(group_name)
            if group is None:
                return ProcessingResult(
                    path=file_path,
                    status=ProcessingStatus.SKIPPED,
                    duration_seconds=time.time() - start,
                    file_type_group=group_name,
                )

            # 3. Route to collection
            is_source = group_name in self._source_groups
            collection = self.router.route(file_path, group_name, is_source)

            # Ensure collection exists
            self.destination.ensure_collection(
                collection,
                self.config.embedding.vector_size,
                self.config.embedding.vector_name,
            )

            # 4. Extract
            text, doc_json = _extract(content, file_path, group, self.source, self.config)
            if not text:
                return ProcessingResult(
                    path=file_path,
                    status=ProcessingStatus.SKIPPED,
                    duration_seconds=time.time() - start,
                    file_type_group=group_name,
                )

            # 5. Compute content hash
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

            # 6. Resolve source URL
            source_url = resolve_source_url(file_path, text)

            # 7. Chunk
            raw_chunks = dispatch_chunker(text, group, doc_json)
            if not raw_chunks:
                return ProcessingResult(
                    path=file_path,
                    status=ProcessingStatus.SKIPPED,
                    duration_seconds=time.time() - start,
                    content_hash=content_hash,
                    file_type_group=group_name,
                )

            # 8. Embed
            chunk_texts = [c["text"] for c in raw_chunks]
            vectors = self.embedder.embed_texts(chunk_texts)

            # 9. Build IndexChunks with metadata
            index_chunks: list[IndexChunk] = []
            for i, (chunk_data, vector) in enumerate(zip(raw_chunks, vectors)):
                point_id = make_point_id(file_path, i)
                payload: dict[str, Any] = {
                    "source": file_path,
                    "source_url": source_url,
                    "content_hash": content_hash,
                    "chunk_index": i,
                    "total_chunks": len(raw_chunks),
                    "collection": collection,
                    "file_size": len(content),
                    "original_format": Path(file_path).suffix,
                    "cache_path": self.source.cache_path(file_path, ".md"),
                    "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "file_type_group": group_name,
                    "chunker_strategy": group.chunker.strategy,
                }
                # Add chunker-specific metadata
                for key in (
                    "headings",
                    "start_line",
                    "end_line",
                    "line_start",
                    "line_end",
                    "routine_name",
                    "is_header",
                ):
                    if key in chunk_data:
                        payload[key] = chunk_data[key]

                index_chunks.append(
                    IndexChunk(
                        point_id=point_id,
                        text=chunk_data["text"],
                        vector=vector,
                        payload=payload,
                    )
                )

            # 10. Index
            self.destination.index_chunks(collection, index_chunks)

            duration = time.time() - start
            logger.info(
                "Processed %s -> %s (%d chunks in %.1fs)",
                file_path,
                collection,
                len(index_chunks),
                duration,
                extra={
                    "file_path": file_path,
                    "collection": collection,
                    "chunk_count": len(index_chunks),
                    "duration_seconds": duration,
                },
            )

            return ProcessingResult(
                path=file_path,
                status=ProcessingStatus.INDEXED,
                duration_seconds=duration,
                collection=collection,
                chunk_count=len(index_chunks),
                content_hash=content_hash,
                file_type_group=group_name,
            )

        except Exception as e:
            duration = time.time() - start
            logger.error(
                "Failed to process %s: %s",
                file_path,
                e,
                extra={"file_path": file_path, "error": str(e), "duration_seconds": duration},
                exc_info=True,
            )
            return ProcessingResult(
                path=file_path,
                status=ProcessingStatus.FAILED,
                duration_seconds=duration,
                error_message=str(e),
                file_type_group=file_type_group,
            )


def _get_source_group_names(groups: dict[str, FileTypeGroup]) -> set[str]:
    """Determine which groups are source-code groups (use raw-text extractor)."""
    return {
        name
        for name, g in groups.items()
        if g.extractor == "raw-text" and g.chunker.strategy != "chonkie-recursive"
    }


def _extract(
    content: bytes,
    file_path: str,
    group: FileTypeGroup,
    source: SourceProvider,
    config: Config,
) -> tuple[str | None, str | None]:
    """Extract text from file content based on group's extractor setting."""
    if group.extractor == "raw-text":
        from thresher.processing.extractors.raw_text import extract_raw_text

        return extract_raw_text(content), None

    elif group.extractor == "docling":
        # Check cache first
        md_cache = source.cache_path(file_path, ".md")
        json_cache = source.cache_path(file_path, ".docling.json")

        if source.exists(md_cache):
            cached_md = source.download_content(md_cache).decode("utf-8")
            cached_json = None
            if source.exists(json_cache):
                cached_json = source.download_content(json_cache).decode("utf-8")
            return cached_md, cached_json

        # Extract via docling subprocess
        import tempfile

        from thresher.processing.extractors.docling import extract_with_docling

        with tempfile.NamedTemporaryFile(suffix=Path(file_path).suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            markdown, doc_json = extract_with_docling(
                tmp_path,
                timeout=config.processing.docling_timeout,
                max_pages=config.processing.max_pages,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        # Cache results
        source.upload_content(md_cache, markdown.encode("utf-8"))
        if doc_json:
            source.upload_content(json_cache, doc_json.encode("utf-8"))

        return markdown, doc_json

    return None, None


def dispatch_chunker(
    text: str,
    group: FileTypeGroup,
    doc_json: str | None = None,
) -> list[dict]:
    """Dispatch to the correct chunker based on file type group config."""
    strategy = group.chunker.strategy
    chunk_size = group.chunker.chunk_size

    if strategy == "docling-hybrid" and doc_json:
        from thresher.processing.chunkers.docling_hybrid import chunk_with_docling_hybrid

        chunks = chunk_with_docling_hybrid(doc_json, chunk_size=chunk_size)
        if chunks:
            return chunks
        # Fall through to recursive if docling hybrid produces nothing

    if strategy == "mumps-label-boundary":
        from thresher.processing.chunkers.mumps_label import chunk_mumps_source

        return chunk_mumps_source(text, chunk_size=chunk_size)

    if strategy == "chonkie-code":
        # Will be implemented in Phase 8 (T047), fall back to recursive for now
        from thresher.processing.chunkers.chonkie_recursive import chunk_with_recursive

        return chunk_with_recursive(text, chunk_size=chunk_size)

    if strategy in ("chonkie-recursive", "docling-hybrid"):
        from thresher.processing.chunkers.chonkie_recursive import chunk_with_recursive

        recipe = group.chunker.recipe
        return chunk_with_recursive(text, chunk_size=chunk_size, recipe=recipe)

    # Unknown strategy — fall back to recursive
    from thresher.processing.chunkers.chonkie_recursive import chunk_with_recursive

    return chunk_with_recursive(text, chunk_size=chunk_size)


def create_source_provider(config: Config) -> SourceProvider:
    """Create a source provider from config (T018d provider factory)."""
    if config.source.provider == "gcs":
        from thresher.providers.gcs import GCSSourceProvider

        return GCSSourceProvider(
            bucket_name=config.source.gcs.bucket,
            source_prefix=config.source.gcs.source_prefix,
            expanded_prefix=config.source.gcs.expanded_prefix,
            cache_prefix=config.source.gcs.cache_prefix,
            queue_prefix=config.source.gcs.queue_prefix,
        )
    raise ValueError(f"Unknown source provider: {config.source.provider}")


def create_destination_provider(config: Config) -> DestinationProvider:
    """Create a destination provider from config (T018d provider factory)."""
    if config.destination.provider == "qdrant":
        from thresher.providers.qdrant import QdrantDestinationProvider

        return QdrantDestinationProvider(
            url=config.destination.qdrant.url,
            api_key=config.destination.qdrant.api_key,
            timeout=config.destination.qdrant.timeout,
            batch_size=config.destination.qdrant.batch_size,
            vector_name=config.embedding.vector_name,
        )
    raise ValueError(f"Unknown destination provider: {config.destination.provider}")
