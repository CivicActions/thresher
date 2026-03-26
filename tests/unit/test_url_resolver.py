"""Tests for thresher.url_resolver."""

from __future__ import annotations

from thresher.url_resolver import (
    UrlResolverConfig,
    _resolve_domain_first,
    _resolve_httrack,
    _resolve_pattern,
    parse_url_resolvers,
    resolve_source_url,
)


class TestHttrackResolver:
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

    def test_httrack_takes_priority(self):
        content = "<!-- Mirrored from specific.example.com/real-page.html -->"
        result = resolve_source_url("source/other.com/file.html", content=content)
        assert result == "https://specific.example.com/real-page.html"

    def test_only_checks_first_4096_bytes(self):
        content = "x" * 4097 + "<!-- Mirrored from example.com/late.html -->"
        result = resolve_source_url("source/example.com/page.html", content=content)
        # Comment is past 4096, so httrack misses it; falls through to domain-first
        assert result == "https://example.com/page.html"

    def test_no_content_skips(self):
        assert _resolve_httrack(None) is None
        assert _resolve_httrack("") is None

    def test_no_comment_returns_none(self):
        assert _resolve_httrack("just some text") is None


class TestPatternResolver:
    def test_simple_pattern(self):
        resolver = UrlResolverConfig(
            type="pattern",
            match=r"^WorldVistA/([^/]+)/(.+)$",
            template="https://github.com/WorldVistA/{1}/blob/HEAD/{2}",
            strip_prefix="source/",
        )
        result = _resolve_pattern("source/WorldVistA/VistA-repo/src/main.py", resolver)
        assert result == "https://github.com/WorldVistA/VistA-repo/blob/HEAD/src/main.py"

    def test_pattern_no_match(self):
        resolver = UrlResolverConfig(
            type="pattern",
            match=r"^WorldVistA/",
            template="https://github.com/WorldVistA/{1}",
        )
        result = _resolve_pattern("other/path/file.py", resolver)
        assert result is None

    def test_pattern_with_strip_prefix(self):
        resolver = UrlResolverConfig(
            type="pattern",
            match=r"^([^/]+)/(.+)$",
            template="https://{1}/{2}",
            strip_prefix="data/",
        )
        result = _resolve_pattern("data/example.com/page.html", resolver)
        assert result == "https://example.com/page.html"

    def test_pattern_empty_match(self):
        resolver = UrlResolverConfig(type="pattern", match="", template="")
        assert _resolve_pattern("anything", resolver) is None

    def test_pattern_in_chain(self):
        resolvers = [
            UrlResolverConfig(type="httrack"),
            UrlResolverConfig(
                type="pattern",
                match=r"^WorldVistA/([^/]+)/(.+)$",
                template="https://github.com/WorldVistA/{1}/blob/HEAD/{2}",
                strip_prefix="source/",
            ),
            UrlResolverConfig(type="domain-first", strip_prefix="source/"),
        ]
        result = resolve_source_url("source/WorldVistA/repo/file.py", resolvers=resolvers)
        assert result == "https://github.com/WorldVistA/repo/blob/HEAD/file.py"


class TestDomainFirstResolver:
    def test_simple_domain_path(self):
        result = _resolve_domain_first("source/example.com/docs/guide.html", strip_prefix="source/")
        assert result == "https://example.com/docs/guide.html"

    def test_domain_only(self):
        result = _resolve_domain_first("source/example.com", strip_prefix="source/")
        assert result == "https://example.com"

    def test_deep_path(self):
        result = _resolve_domain_first(
            "source/docs.example.org/a/b/c/page.html", strip_prefix="source/"
        )
        assert result == "https://docs.example.org/a/b/c/page.html"

    def test_no_prefix(self):
        result = _resolve_domain_first("example.com/page.html")
        assert result == "https://example.com/page.html"


class TestDefaultResolverChain:
    def test_default_chain_httrack_first(self):
        content = "<!-- Mirrored from example.com/page.html -->"
        result = resolve_source_url("source/other.com/file.html", content=content)
        assert result == "https://example.com/page.html"

    def test_default_chain_falls_to_domain_first(self):
        result = resolve_source_url("source/example.com/docs/guide.html")
        assert result == "https://example.com/docs/guide.html"

    def test_empty_resolvers_uses_defaults(self):
        result = resolve_source_url("source/example.com/page.html", resolvers=None)
        assert result == "https://example.com/page.html"


class TestParseUrlResolvers:
    def test_parse_empty(self):
        assert parse_url_resolvers(None) == []
        assert parse_url_resolvers([]) == []

    def test_parse_httrack(self):
        resolvers = parse_url_resolvers([{"type": "httrack"}])
        assert len(resolvers) == 1
        assert resolvers[0].type == "httrack"

    def test_parse_pattern(self):
        resolvers = parse_url_resolvers(
            [
                {
                    "type": "pattern",
                    "match": "^foo/(.*)",
                    "template": "https://example.com/{1}",
                    "strip_prefix": "source/",
                }
            ]
        )
        assert len(resolvers) == 1
        assert resolvers[0].match == "^foo/(.*)"
        assert resolvers[0].template == "https://example.com/{1}"
        assert resolvers[0].strip_prefix == "source/"

    def test_parse_skips_non_dicts(self):
        resolvers = parse_url_resolvers(["invalid", {"type": "httrack"}])
        assert len(resolvers) == 1


class TestSourcePrefixStripping:
    def test_strips_source_prefix(self):
        result = resolve_source_url("source/example.com/path")
        assert result == "https://example.com/path"

    def test_no_prefix_works(self):
        result = resolve_source_url("example.com/path")
        assert result == "https://example.com/path"
