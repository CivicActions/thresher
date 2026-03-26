"""Tests for custom content detectors and image size threshold (T033, T037)."""

from __future__ import annotations

import pytest

from thresher.processing.classifier import (
    DETECTORS,
    _detect_caret_density,
    _detect_mumps_labels,
    classify_file,
    should_skip_image,
)
from thresher.types import ChunkerConfig, FileTypeGroup

# ---------------------------------------------------------------------------
# T033 — MUMPS label detector
# ---------------------------------------------------------------------------


class TestMumpsLabelDetector:
    def test_typical_mumps_source(self):
        content = (
            b"HELLO ; say hello\n"
            b' W "Hello, World!",!\n'
            b" Q\n"
            b"ADD(X,Y) ; add two numbers\n"
            b" Q X+Y\n"
            b"%ROUTINE ; percent-prefixed label\n"
            b" Q\n"
        )
        assert _detect_mumps_labels(content, "test.m") is True

    def test_single_label_not_enough(self):
        content = b"LABEL ; only one label\n W 1\n"
        assert _detect_mumps_labels(content, "test.m") is False

    def test_no_labels(self):
        content = b"def hello():\n    print('hi')\n"
        assert _detect_mumps_labels(content, "test.py") is False

    def test_empty_content(self):
        assert _detect_mumps_labels(b"", "test.m") is False

    def test_percent_prefixed_labels(self):
        content = b"%A ; a\n%B ; b\n%C ; c\n"
        assert _detect_mumps_labels(content, "test.m") is True


# ---------------------------------------------------------------------------
# T033 — Caret density detector
# ---------------------------------------------------------------------------


class TestCaretDensityDetector:
    def test_high_density(self):
        # 10% carets = above 5% threshold
        content = b"^" * 10 + b"x" * 90
        assert _detect_caret_density(content, "test.zwr") is True

    def test_low_density(self):
        # 1% carets = below 5% threshold
        content = b"^" * 1 + b"x" * 99
        assert _detect_caret_density(content, "test.zwr") is False

    def test_zero_carets(self):
        content = b"no carets here at all"
        assert _detect_caret_density(content, "test.txt") is False

    def test_empty_content(self):
        assert _detect_caret_density(b"", "test.zwr") is False

    def test_typical_zwr_content(self):
        # Simulated ZWR globals export: lots of ^-prefixed global refs
        # Short keys to keep caret density above 5%
        lines = [b"^G(%d)=%d\n" % (i, i) for i in range(100)]
        content = b"".join(lines)
        assert _detect_caret_density(content, "globals.zwr") is True


# ---------------------------------------------------------------------------
# T033 — Detector registry
# ---------------------------------------------------------------------------


class TestDetectorRegistry:
    def test_mumps_labels_registered(self):
        assert "mumps-labels" in DETECTORS
        assert DETECTORS["mumps-labels"] is _detect_mumps_labels

    def test_caret_density_registered(self):
        assert "caret-density" in DETECTORS
        assert DETECTORS["caret-density"] is _detect_caret_density


# ---------------------------------------------------------------------------
# T033 — Integration with classify_file
# ---------------------------------------------------------------------------


class TestDetectorIntegration:
    def test_detector_match_classifies_group(self):
        """A file with no matching extension but matching detector should classify."""
        groups = {
            "mumps-source": FileTypeGroup(
                name="mumps-source",
                extensions=[".m", ".ro"],
                detectors=["mumps-labels"],
                extractor="raw-text",
                chunker=ChunkerConfig(strategy="mumps-label-boundary"),
                priority=30,
            ),
        }
        # File has no matching extension but content triggers detector
        content = b"HELLO ; hi\nADD(X,Y) ; add\n Q X+Y\nSUB(X,Y)\n Q X-Y\n"
        result = classify_file("unknown_file.dat", groups, content=content)
        assert result == "mumps-source"

    def test_detector_no_match_returns_none(self):
        groups = {
            "mumps-source": FileTypeGroup(
                name="mumps-source",
                extensions=[".m", ".ro"],
                detectors=["mumps-labels"],
                extractor="raw-text",
                chunker=ChunkerConfig(strategy="mumps-label-boundary"),
                priority=30,
            ),
        }
        content = b"print('hello world')\n"
        result = classify_file("unknown_file.dat", groups, content=content)
        assert result is None

    def test_extension_wins_over_detector_at_same_priority(self):
        """When a higher-priority group matches by extension, detector groups don't fire."""
        groups = {
            "general-source": FileTypeGroup(
                name="general-source",
                extensions=[".py"],
                extractor="raw-text",
                chunker=ChunkerConfig(strategy="chonkie-code"),
                priority=20,
            ),
            "mumps-source": FileTypeGroup(
                name="mumps-source",
                extensions=[".m", ".ro"],
                detectors=["mumps-labels"],
                extractor="raw-text",
                chunker=ChunkerConfig(strategy="mumps-label-boundary"),
                priority=30,
            ),
        }
        content = b"HELLO ; hi\nADD(X,Y)\n Q\nSUB(X,Y)\n Q\n"
        # .py extension matches general-source (priority 20, checked first)
        result = classify_file("script.py", groups, content=content)
        assert result == "general-source"

    def test_unknown_detector_ignored(self):
        """A detector name not in the registry is silently skipped."""
        groups = {
            "custom": FileTypeGroup(
                name="custom",
                detectors=["nonexistent-detector"],
                extractor="raw-text",
                priority=10,
            ),
        }
        result = classify_file("file.dat", groups, content=b"some content")
        assert result is None

    def test_caret_density_detector_integration(self):
        """Caret density detector correctly classifies via classify_file."""
        groups = {
            "mumps-globals": FileTypeGroup(
                name="mumps-globals",
                extensions=[".zwr"],
                detectors=["caret-density"],
                extractor="raw-text",
                chunker=ChunkerConfig(strategy="mumps-label-boundary"),
                priority=30,
            ),
        }
        # High caret density content, no matching extension
        content = b"^" * 10 + b"x" * 90
        result = classify_file("data.dat", groups, content=content)
        assert result == "mumps-globals"


# ---------------------------------------------------------------------------
# T037 — Image size threshold
# ---------------------------------------------------------------------------


class TestShouldSkipImage:
    def test_small_image_skipped(self):
        assert should_skip_image("photo.jpg", 1000, min_size=51200) is True

    def test_large_image_not_skipped(self):
        assert should_skip_image("photo.jpg", 100000, min_size=51200) is False

    def test_exact_threshold_not_skipped(self):
        assert should_skip_image("photo.png", 51200, min_size=51200) is False

    def test_non_image_not_skipped(self):
        assert should_skip_image("doc.pdf", 100, min_size=51200) is False

    def test_none_size_not_skipped(self):
        assert should_skip_image("photo.jpg", None, min_size=51200) is False

    def test_various_image_extensions(self):
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".svg", ".webp"]:
            assert should_skip_image(f"img{ext}", 100, min_size=51200) is True

    def test_case_insensitive_extension(self):
        assert should_skip_image("photo.JPG", 100, min_size=51200) is True
        assert should_skip_image("photo.Png", 100, min_size=51200) is True


# ---------------------------------------------------------------------------
# MIME-type classification for extensionless files
# ---------------------------------------------------------------------------


class TestMimeTypeClassification:
    """Test that files without extensions are classified via MIME type detection."""

    @pytest.fixture
    def default_groups(self):
        """Load the default file type groups from config."""
        from thresher.config import load_config

        cfg = load_config()
        return cfg.file_type_groups

    def test_extensionless_pdf(self, default_groups):
        content = b"%PDF-1.4 fake pdf content here with enough bytes to detect"
        result = classify_file("data/mystery_document", default_groups, content=content)
        assert result == "office-documents"

    def test_extensionless_json(self, default_groups):
        content = b'{"key": "value", "number": 42}'
        result = classify_file("data/api_response", default_groups, content=content)
        assert result == "data-files"

    def test_extensionless_xml(self, default_groups):
        content = b'<?xml version="1.0"?><root><item>hello</item></root>'
        result = classify_file("data/config_file", default_groups, content=content)
        assert result == "data-files"

    def test_extensionless_plain_text(self, default_groups):
        content = b"This is just a plain text file without any extension at all."
        result = classify_file("data/readme", default_groups, content=content)
        assert result == "plain-text"

    def test_extensionless_shell_script(self, default_groups):
        content = b"#!/bin/bash\necho hello\nexit 0\n"
        result = classify_file("scripts/deploy", default_groups, content=content)
        assert result == "general-source"

    def test_extensionless_python_script(self, default_groups):
        content = b"#!/usr/bin/env python3\nimport os\nprint(os.getcwd())\n"
        result = classify_file("tools/check", default_groups, content=content)
        assert result == "general-source"

    def test_extensionless_binary_returns_none(self, default_groups):
        content = b"\x00\x01\x02\x03\xff\xfe binary garbage"
        result = classify_file("data/unknown_blob", default_groups, content=content)
        assert result is None

    def test_no_content_no_extension_returns_none(self, default_groups):
        result = classify_file("data/mystery", default_groups, content=None)
        assert result is None
