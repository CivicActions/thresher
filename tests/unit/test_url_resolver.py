"""Tests for thresher.url_resolver."""

from __future__ import annotations

from thresher.url_resolver import resolve_source_url


class TestHttrackExtraction:
    def test_extracts_url_from_mirror_comment(self):
        content = "<!-- Mirrored from example.com/page.html by HTTrack -->"
        result = resolve_source_url("source/example.com/page.html", content=content)
        assert result == "https://example.com/page.html"

    def test_extracts_url_with_http(self):
        content = "<!-- Mirrored from http://example.com/page.html by HTTrack -->"
        result = resolve_source_url("source/example.com/page.html", content=content)
        assert result == "http://example.com/page.html"

    def test_extracts_url_with_https(self):
        content = "<!-- Mirrored from https://example.com/path/to/page.html -->"
        result = resolve_source_url("source/example.com/path/to/page.html", content=content)
        assert result == "https://example.com/path/to/page.html"

    def test_httrack_takes_priority_over_other_methods(self):
        content = "<!-- Mirrored from specific.example.com/real-page.html -->"
        result = resolve_source_url("source/WorldVistA/repo/file.html", content=content)
        assert result == "https://specific.example.com/real-page.html"

    def test_only_checks_first_4096_bytes(self):
        # Comment after 4096 bytes should not be found
        content = "x" * 4097 + "<!-- Mirrored from example.com/late.html -->"
        result = resolve_source_url("source/example.com/page.html", content=content)
        # Should fall through to domain-first since comment is past 4096
        assert result == "https://example.com/page.html"


class TestWorldVistaMapping:
    def test_worldvista_repo_file(self):
        result = resolve_source_url("source/WorldVistA/VistA-repo/src/main.py")
        assert result == "https://github.com/WorldVistA/VistA-repo/blob/HEAD/src/main.py"

    def test_worldvista_repo_only(self):
        result = resolve_source_url("source/WorldVistA/VistA-repo")
        assert result == "https://github.com/WorldVistA/VistA-repo"

    def test_worldvista_deep_path(self):
        result = resolve_source_url("source/WorldVistA/MyRepo/a/b/c/file.txt")
        assert result == "https://github.com/WorldVistA/MyRepo/blob/HEAD/a/b/c/file.txt"

    def test_worldvista_no_content_override(self):
        """Without httrack content, WorldVistA mapping should be used."""
        result = resolve_source_url("source/WorldVistA/repo/file.py", content="no comment here")
        assert result == "https://github.com/WorldVistA/repo/blob/HEAD/file.py"


class TestDomainFirstFallback:
    def test_simple_domain_path(self):
        result = resolve_source_url("source/example.com/docs/guide.html")
        assert result == "https://example.com/docs/guide.html"

    def test_domain_only(self):
        result = resolve_source_url("source/example.com")
        assert result == "https://example.com"

    def test_deep_path(self):
        result = resolve_source_url("source/docs.example.org/a/b/c/page.html")
        assert result == "https://docs.example.org/a/b/c/page.html"

    def test_no_source_prefix(self):
        result = resolve_source_url("example.com/page.html")
        assert result == "https://example.com/page.html"


class TestSourcePrefixStripping:
    def test_strips_source_prefix(self):
        result = resolve_source_url("source/example.com/path")
        assert result == "https://example.com/path"

    def test_no_prefix_works(self):
        result = resolve_source_url("example.com/path")
        assert result == "https://example.com/path"

    def test_double_source_prefix_strips_once(self):
        result = resolve_source_url("source/source/example.com/path")
        assert result == "https://source/example.com/path"
