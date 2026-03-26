"""Tests for all three chunker implementations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from thresher.processing.chunkers.chonkie_recursive import (
    _simple_split,
    chunk_with_recursive,
)
from thresher.processing.chunkers.docling_hybrid import chunk_with_docling_hybrid
from thresher.processing.chunkers.mumps_label import chunk_mumps_source

# ---------------------------------------------------------------------------
# Sample MUMPS source for tests
# ---------------------------------------------------------------------------
MUMPS_SAMPLE = """\
TESTRTN ;Package - Test routine ;3.0;Build 1
 ;;1.0;TEST;**1**;Mar 01, 2024
 Q
HELLO(NAME) ;Say hello
 W "Hello, "_NAME,!
 Q
GOODBYE ;Say goodbye
 W "Goodbye!",!
 Q"""


# ===================================================================
# Docling HybridChunker tests
# ===================================================================
class TestDoclingHybridChunker:
    """Tests for chunk_with_docling_hybrid."""

    def test_import_error_returns_empty(self):
        """When docling_core is not installed, return empty list."""
        with patch.dict(
            "sys.modules",
            {
                "docling_core": None,
                "docling_core.types.doc": None,
                "docling_core.transforms.chunker.hybrid_chunker": None,
            },
        ):
            # Force re-import failure by patching builtins
            original_import = (
                __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
            )

            def fail_import(name, *args, **kwargs):
                if "docling_core" in name:
                    raise ImportError("mocked")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fail_import):
                result = chunk_with_docling_hybrid('{"some": "json"}')
            assert result == []

    def test_invalid_json_returns_empty(self):
        """When JSON is invalid for DoclingDocument, return empty list."""
        mock_doc_class = MagicMock()
        mock_doc_class.model_validate_json.side_effect = ValueError("bad json")

        mock_chunker_class = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "docling_core": MagicMock(),
                "docling_core.types": MagicMock(),
                "docling_core.types.doc": MagicMock(DoclingDocument=mock_doc_class),
                "docling_core.transforms": MagicMock(),
                "docling_core.transforms.chunker": MagicMock(),
                "docling_core.transforms.chunker.hybrid_chunker": MagicMock(
                    HybridChunker=mock_chunker_class
                ),
            },
        ):
            result = chunk_with_docling_hybrid('{"bad": "data"}')
        assert result == []

    def test_successful_chunking(self):
        """Mocked successful chunking returns expected structure."""
        mock_doc = MagicMock()
        mock_doc_class = MagicMock()
        mock_doc_class.model_validate_json.return_value = mock_doc

        heading = SimpleNamespace(text="Chapter 1")
        chunk1 = SimpleNamespace(
            text="First chunk text",
            meta=SimpleNamespace(headings=[heading]),
        )
        chunk2 = SimpleNamespace(
            text="Second chunk text",
            meta=None,
        )

        mock_chunker_instance = MagicMock()
        mock_chunker_instance.chunk.return_value = [chunk1, chunk2]
        mock_chunker_class = MagicMock(return_value=mock_chunker_instance)

        with patch.dict(
            "sys.modules",
            {
                "docling_core": MagicMock(),
                "docling_core.types": MagicMock(),
                "docling_core.types.doc": MagicMock(DoclingDocument=mock_doc_class),
                "docling_core.transforms": MagicMock(),
                "docling_core.transforms.chunker": MagicMock(),
                "docling_core.transforms.chunker.hybrid_chunker": MagicMock(
                    HybridChunker=mock_chunker_class
                ),
            },
        ):
            result = chunk_with_docling_hybrid('{"valid": "json"}', chunk_size=256)

        assert len(result) == 2
        assert result[0]["text"] == "First chunk text"
        assert result[0]["headings"] == ["Chapter 1"]
        assert result[1]["text"] == "Second chunk text"
        assert result[1]["headings"] == []

    def test_empty_document_json(self):
        """Empty string triggers parse failure, returns empty."""
        mock_doc_class = MagicMock()
        mock_doc_class.model_validate_json.side_effect = Exception("empty")
        mock_chunker_class = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "docling_core": MagicMock(),
                "docling_core.types": MagicMock(),
                "docling_core.types.doc": MagicMock(DoclingDocument=mock_doc_class),
                "docling_core.transforms": MagicMock(),
                "docling_core.transforms.chunker": MagicMock(),
                "docling_core.transforms.chunker.hybrid_chunker": MagicMock(
                    HybridChunker=mock_chunker_class
                ),
            },
        ):
            result = chunk_with_docling_hybrid("")
        assert result == []


# ===================================================================
# MUMPS label-boundary chunker tests
# ===================================================================
class TestMumpsLabelChunker:
    """Tests for chunk_mumps_source."""

    def test_sample_produces_header_and_two_labels(self):
        """Standard MUMPS sample should produce a header + HELLO + GOODBYE."""
        chunks = chunk_mumps_source(MUMPS_SAMPLE)

        # Header before first label (TESTRTN is a label, so only if there are
        # lines before it). TESTRTN is on line 1, so no header; 3 label chunks.
        routine_names = [c["routine_name"] for c in chunks]
        assert "TESTRTN" in routine_names
        assert "HELLO" in routine_names
        assert "GOODBYE" in routine_names

    def test_header_detection(self):
        """Lines before the first label form a header chunk."""
        source = "; This is a header comment\n; Another comment\nLABEL1 ;code\n Q\n"
        chunks = chunk_mumps_source(source)

        header_chunks = [c for c in chunks if c["is_header"]]
        assert len(header_chunks) == 1
        assert header_chunks[0]["routine_name"] == "_header"

        label_chunks = [c for c in chunks if not c["is_header"]]
        assert len(label_chunks) == 1
        assert label_chunks[0]["routine_name"] == "LABEL1"

    def test_line_numbers_are_correct(self):
        """Line numbers should be 1-based and cover the full file."""
        chunks = chunk_mumps_source(MUMPS_SAMPLE)

        # First chunk should start at line 1
        assert chunks[0]["line_start"] == 1

        # Last chunk should end at total line count
        total_lines = len(MUMPS_SAMPLE.split("\n"))
        assert chunks[-1]["line_end"] == total_lines

        # No gaps between chunks
        for i in range(1, len(chunks)):
            assert chunks[i]["line_start"] == chunks[i - 1]["line_end"] + 1

    def test_no_labels_entire_file_is_one_chunk(self):
        """File with no labels should be treated as a single section."""
        source = " ; just comments\n ; more comments\n Q\n"
        chunks = chunk_mumps_source(source)
        assert len(chunks) == 1
        assert chunks[0]["routine_name"] == "_file"
        assert chunks[0]["is_header"] is True

    def test_oversized_section_splitting(self):
        """Sections exceeding chunk_size should be split."""
        # Create a section that is very large
        big_section = "BIGLABEL ;start\n" + "\n".join([f" S X={i}" for i in range(200)])
        # Use a very small chunk_size to force splitting
        chunks = chunk_mumps_source(big_section, chunk_size=10)
        assert len(chunks) > 1
        assert all(c["routine_name"] == "BIGLABEL" for c in chunks)

    def test_empty_text(self):
        """Empty text produces a single empty-ish chunk."""
        chunks = chunk_mumps_source("")
        # Empty text has one line (empty string), treated as _file
        assert len(chunks) == 1
        assert chunks[0]["routine_name"] == "_file"

    def test_custom_token_counter(self):
        """Custom count_tokens function is respected."""
        # Token counter that always returns 1 — nothing is oversized
        chunks = chunk_mumps_source(MUMPS_SAMPLE, chunk_size=5, count_tokens=lambda t: 1)
        # Should not split anything since everything fits
        for c in chunks:
            assert "text" in c

    def test_percent_label(self):
        """Labels starting with % should be recognized."""
        source = "%ZSTART ;entry\n W 1\n Q\n"
        chunks = chunk_mumps_source(source)
        assert chunks[0]["routine_name"] == "%ZSTART"


# ===================================================================
# Chonkie RecursiveChunker tests
# ===================================================================
class TestChonkieRecursiveChunker:
    """Tests for chunk_with_recursive."""

    def test_empty_text_returns_empty(self):
        """Empty or whitespace-only text returns empty list."""
        assert chunk_with_recursive("") == []
        assert chunk_with_recursive("   \n  ") == []

    def test_import_error_falls_back_to_simple_split(self):
        """When chonkie is not installed, use _simple_split fallback."""
        original_import = (
            __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
        )

        def fail_import(name, *args, **kwargs):
            if name == "chonkie":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_import):
            result = chunk_with_recursive("Hello world, this is a test.")
        assert len(result) >= 1
        assert result[0]["text"].startswith("Hello")

    def test_successful_chunking_no_recipe(self):
        """Mocked chunking without recipe returns expected structure."""
        mock_chunk = SimpleNamespace(
            text="chunk one",
            start_index=0,
            end_index=9,
            token_count=3,
        )
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = [mock_chunk]
        mock_class = MagicMock(return_value=mock_chunker)

        with patch.dict(
            "sys.modules",
            {
                "chonkie": MagicMock(RecursiveChunker=mock_class),
            },
        ):
            result = chunk_with_recursive("chunk one text here")

        assert len(result) == 1
        assert result[0]["text"] == "chunk one"
        assert result[0]["start_index"] == 0
        assert result[0]["end_index"] == 9
        assert result[0]["token_count"] == 3

        # Verify RecursiveChunker was called without from_recipe
        mock_class.assert_called_once()
        mock_class.from_recipe.assert_not_called()

    def test_recipe_parameter_uses_from_recipe(self):
        """When recipe is provided, from_recipe class method is used."""
        mock_chunk = SimpleNamespace(
            text="md chunk",
            start_index=0,
            end_index=8,
            token_count=2,
        )
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = [mock_chunk]

        mock_class = MagicMock()
        mock_class.from_recipe.return_value = mock_chunker

        with patch.dict(
            "sys.modules",
            {
                "chonkie": MagicMock(RecursiveChunker=mock_class),
            },
        ):
            result = chunk_with_recursive("# Heading\nContent", recipe="markdown")

        assert len(result) == 1
        mock_class.from_recipe.assert_called_once_with(
            "markdown",
            tokenizer="sentence-transformers/all-MiniLM-L6-v2",
            chunk_size=512,
            min_characters_per_chunk=24,
        )

    def test_simple_split_fallback(self):
        """_simple_split produces valid chunks with correct structure."""
        text = "Hello world. " * 100
        chunks = _simple_split(text, chunk_size=10)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert "text" in chunk
            assert "start_index" in chunk
            assert "end_index" in chunk
            assert "token_count" in chunk
            assert chunk["start_index"] < chunk["end_index"]

    def test_simple_split_empty_text(self):
        """_simple_split with empty text returns empty list."""
        assert _simple_split("", 512) == []
        assert _simple_split("   ", 512) == []

    def test_simple_split_breaks_at_newline(self):
        """_simple_split should prefer breaking at newline boundaries."""
        # Create text with newlines that's larger than one chunk
        lines = ["Line " + str(i) for i in range(100)]
        text = "\n".join(lines)
        chunks = _simple_split(text, chunk_size=10)
        # Each chunk (except possibly the last) should end at a newline
        for chunk in chunks[:-1]:
            assert chunk["text"].endswith("\n") or chunk["end_index"] == len(text)
