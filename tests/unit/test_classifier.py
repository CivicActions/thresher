"""Tests for thresher.processing.classifier."""

from __future__ import annotations

from unittest.mock import patch

from thresher.processing.classifier import _is_binary, classify_file
from thresher.types import ChunkerConfig, FileTypeGroup


def _make_groups() -> dict[str, FileTypeGroup]:
    """Build a representative set of file type groups for testing."""
    return {
        "office-documents": FileTypeGroup(
            name="office-documents",
            extensions=[".pdf", ".docx", ".xlsx", ".pptx"],
            mime_types=["application/pdf", "application/vnd.openxmlformats"],
            extractor="docling",
            chunker=ChunkerConfig(strategy="docling-hybrid"),
            priority=50,
        ),
        "source-code": FileTypeGroup(
            name="source-code",
            extensions=[".py", ".js", ".ts", ".java", ".go"],
            mime_types=["text/x-python", "text/javascript"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="chonkie-code", language="auto"),
            priority=60,
        ),
        "web-content": FileTypeGroup(
            name="web-content",
            extensions=[".html", ".htm", ".css"],
            mime_types=["text/html"],
            extractor="raw-text",
            chunker=ChunkerConfig(strategy="chonkie-recursive"),
            priority=70,
        ),
        "binary-skip": FileTypeGroup(
            name="binary-skip",
            extensions=[".exe", ".dll", ".bin"],
            mime_types=[],
            extractor="skip",
            priority=999,
        ),
    }


class TestClassifyByExtension:
    def test_pdf_matches_office(self):
        groups = _make_groups()
        assert classify_file("report.pdf", groups) == "office-documents"

    def test_docx_matches_office(self):
        groups = _make_groups()
        assert classify_file("doc.docx", groups) == "office-documents"

    def test_python_matches_source(self):
        groups = _make_groups()
        assert classify_file("main.py", groups) == "source-code"

    def test_html_matches_web(self):
        groups = _make_groups()
        assert classify_file("index.html", groups) == "web-content"

    def test_case_insensitive_extension(self):
        groups = _make_groups()
        assert classify_file("REPORT.PDF", groups) == "office-documents"

    def test_unknown_extension_returns_none(self):
        groups = _make_groups()
        assert classify_file("data.xyz", groups) is None

    def test_no_extension_returns_none(self):
        groups = _make_groups()
        assert classify_file("Makefile", groups) is None

    def test_nested_path_extracts_extension(self):
        groups = _make_groups()
        assert classify_file("src/main/App.java", groups) == "source-code"


class TestPriorityOrdering:
    def test_lower_priority_checked_first(self):
        """When a file matches two groups, lower priority wins."""
        groups = {
            "high-pri": FileTypeGroup(
                name="high-pri",
                extensions=[".md"],
                extractor="raw-text",
                priority=10,
            ),
            "low-pri": FileTypeGroup(
                name="low-pri",
                extensions=[".md"],
                extractor="raw-text",
                priority=90,
            ),
        }
        assert classify_file("README.md", groups) == "high-pri"

    def test_skip_extractor_groups_ignored(self):
        """Groups with extractor='skip' are not matched."""
        groups = {
            "binary": FileTypeGroup(
                name="binary",
                extensions=[".bin"],
                extractor="skip",
                priority=1,
            ),
        }
        assert classify_file("data.bin", groups) is None


class TestMimeTypeMatching:
    @patch("thresher.processing.classifier._detect_mime_type")
    def test_mime_prefix_match(self, mock_detect):
        mock_detect.return_value = "application/pdf"
        groups = _make_groups()
        # Use an extension that won't match any group
        result = classify_file("unknown_file.xyz", groups, content=b"fake-pdf")
        assert result == "office-documents"

    @patch("thresher.processing.classifier._detect_mime_type")
    def test_mime_no_match(self, mock_detect):
        mock_detect.return_value = "application/octet-stream"
        groups = _make_groups()
        result = classify_file("unknown.xyz", groups, content=b"binary-stuff")
        assert result is None

    @patch("thresher.processing.classifier._detect_mime_type")
    def test_mime_none_returned(self, mock_detect):
        mock_detect.return_value = None
        groups = _make_groups()
        result = classify_file("unknown.xyz", groups, content=b"data")
        assert result is None


class TestBinaryDetection:
    def test_binary_content_detected(self):
        binary = b"\x00\x01\x02\x03\x04"
        assert _is_binary(binary) is True

    def test_text_content_not_binary(self):
        text = b"Hello, world!\nThis is plain text."
        assert _is_binary(text) is False

    def test_binary_file_returns_none(self):
        groups = _make_groups()
        binary_content = b"\x00\x01\x02\x03"
        result = classify_file("unknown.xyz", groups, content=binary_content)
        assert result is None

    def test_empty_content_not_binary(self):
        assert _is_binary(b"") is False
