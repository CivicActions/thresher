"""Tests for thresher.processing.router."""

from __future__ import annotations

from thresher.processing.router import Router, _path_matches
from thresher.types import RouteResult, RoutingRule


def _make_router(rules: list[RoutingRule] | None = None) -> Router:
    return Router(
        rules=rules or [],
        default_collection="default",
    )


def _route_expect(router: Router, path: str, **kwargs: str | None) -> RouteResult:
    """Call router.route and assert the result is not None."""
    result = router.route(path, **kwargs)
    assert result is not None
    return result


class TestDefaultRouting:
    def test_no_rules_returns_default(self):
        router = _make_router()
        assert _route_expect(router, "docs/file.pdf").collection == "default"

    def test_source_routing_via_rule(self):
        rules = [
            RoutingRule(
                collection="vista-source",
                file_group=["mumps-source", "general-source"],
            )
        ]
        router = _make_router(rules)
        result = _route_expect(router, "src/main.py", file_type_group="general-source")
        assert result.collection == "vista-source"

    def test_no_source_rule_uses_default(self):
        router = _make_router()
        result = _route_expect(router, "src/main.py", file_type_group="general-source")
        assert result.collection == "default"

    def test_custom_default_collection(self):
        router = Router(rules=[], default_collection="custom")
        assert _route_expect(router, "file.txt").collection == "custom"


class TestFirstMatchWins:
    def test_first_matching_rule_wins(self):
        rules = [
            RoutingRule(collection="docs-collection", file_group=["office-documents"]),
            RoutingRule(collection="catch-all", file_group=["office-documents"]),
        ]
        router = _make_router(rules)
        result = _route_expect(router, "report.pdf", file_type_group="office-documents")
        assert result.collection == "docs-collection"

    def test_non_matching_rule_skipped(self):
        rules = [
            RoutingRule(collection="code-coll", file_group=["source-code"]),
            RoutingRule(collection="docs-coll", file_group=["office-documents"]),
        ]
        router = _make_router(rules)
        result = _route_expect(router, "report.pdf", file_type_group="office-documents")
        assert result.collection == "docs-coll"


class TestFileGroupCriterion:
    def test_file_group_match(self):
        rules = [RoutingRule(collection="target", file_group=["web-content", "office-documents"])]
        router = _make_router(rules)
        result = _route_expect(router, "page.html", file_type_group="web-content")
        assert result.collection == "target"

    def test_file_group_no_match(self):
        rules = [RoutingRule(collection="target", file_group=["web-content"])]
        router = _make_router(rules)
        result = _route_expect(router, "main.py", file_type_group="source-code")
        assert result.collection == "default"

    def test_file_group_none_type(self):
        rules = [RoutingRule(collection="target", file_group=["web-content"])]
        router = _make_router(rules)
        result = _route_expect(router, "unknown.xyz", file_type_group=None)
        assert result.collection == "default"


class TestPathCriterion:
    def test_path_substring_match(self):
        rules = [RoutingRule(collection="legacy", path=["legacy/"])]
        router = _make_router(rules)
        assert _route_expect(router, "legacy/old_doc.pdf").collection == "legacy"

    def test_path_no_match(self):
        rules = [RoutingRule(collection="legacy", path=["legacy/"])]
        router = _make_router(rules)
        assert _route_expect(router, "current/new_doc.pdf").collection == "default"

    def test_path_regex_match(self):
        rules = [RoutingRule(collection="versioned", path=[r"^v\d+/"])]
        router = _make_router(rules)
        assert _route_expect(router, "v2/release-notes.md").collection == "versioned"

    def test_path_regex_no_match(self):
        rules = [RoutingRule(collection="versioned", path=[r"^v\d+/"])]
        router = _make_router(rules)
        assert _route_expect(router, "docs/v2-notes.md").collection == "default"

    def test_path_or_semantics(self):
        rules = [RoutingRule(collection="archive", path=["old/", "archive/"])]
        router = _make_router(rules)
        assert _route_expect(router, "old/doc.pdf").collection == "archive"
        assert _route_expect(router, "archive/doc.pdf").collection == "archive"
        assert _route_expect(router, "new/doc.pdf").collection == "default"


class TestFilenameCriterion:
    def test_filename_glob_match(self):
        rules = [RoutingRule(collection="readmes", filename=["README*"])]
        router = _make_router(rules)
        assert _route_expect(router, "project/README.md").collection == "readmes"

    def test_filename_exact_match(self):
        rules = [RoutingRule(collection="licenses", filename=["LICENSE"])]
        router = _make_router(rules)
        assert _route_expect(router, "project/LICENSE").collection == "licenses"

    def test_filename_no_match(self):
        rules = [RoutingRule(collection="readmes", filename=["README*"])]
        router = _make_router(rules)
        assert _route_expect(router, "project/main.py").collection == "default"

    def test_filename_or_semantics(self):
        rules = [RoutingRule(collection="meta", filename=["README*", "LICENSE*", "CHANGELOG*"])]
        router = _make_router(rules)
        assert _route_expect(router, "p/README.md").collection == "meta"
        assert _route_expect(router, "p/LICENSE").collection == "meta"
        assert _route_expect(router, "p/CHANGELOG.md").collection == "meta"


class TestAndSemantics:
    def test_file_group_and_path_both_required(self):
        rules = [
            RoutingRule(
                collection="special",
                file_group=["office-documents"],
                path=["important/"],
            )
        ]
        router = _make_router(rules)
        # Both match
        result = _route_expect(router, "important/report.pdf", file_type_group="office-documents")
        assert result.collection == "special"
        # Only path matches
        result2 = _route_expect(router, "important/main.py", file_type_group="source-code")
        assert result2.collection == "default"
        # Only group matches
        result3 = _route_expect(router, "other/report.pdf", file_type_group="office-documents")
        assert result3.collection == "default"

    def test_all_three_criteria(self):
        rules = [
            RoutingRule(
                collection="precise",
                file_group=["web-content"],
                path=["public/"],
                filename=["index.*"],
            )
        ]
        router = _make_router(rules)
        result = _route_expect(router, "public/index.html", file_type_group="web-content")
        assert result.collection == "precise"
        result2 = _route_expect(router, "public/about.html", file_type_group="web-content")
        assert result2.collection == "default"

    def test_empty_rule_never_matches(self):
        """A rule with no criteria should not match anything."""
        rules = [RoutingRule(collection="empty")]
        router = _make_router(rules)
        assert _route_expect(router, "anything.txt").collection == "default"


class TestPathMatches:
    def test_substring(self):
        assert _path_matches("foo/bar/baz.txt", "bar/") is True

    def test_substring_no_match(self):
        assert _path_matches("foo/bar/baz.txt", "qux/") is False

    def test_regex_start(self):
        assert _path_matches("v2/release.md", r"^v\d+/") is True

    def test_regex_end(self):
        assert _path_matches("docs/readme.md", r"\.md$") is True

    def test_regex_no_match(self):
        assert _path_matches("docs/readme.txt", r"\.md$") is False

    def test_regex_case_insensitive_by_default(self):
        assert _path_matches("source/IHS.gov/doc.pdf", r"^source/ihs\.gov") is True

    def test_inline_flag_stripped_and_works(self):
        """Patterns with (?i) inline flags work on Python 3.13+."""
        assert _path_matches("source/IHS.gov/file.txt", r"^(?i).*ihs\.gov") is True

    def test_inline_flag_mid_pattern(self):
        """(?i) anywhere in pattern is stripped, re.IGNORECASE used instead."""
        assert _path_matches("source/RPMS/data.csv", r"^(?i).*rpms") is True

    def test_substring_case_insensitive(self):
        assert _path_matches("source/IHS.gov/file.txt", "ihs.gov") is True


# ---------------------------------------------------------------------------
# RouteResult and embedding field tests (T012)
# ---------------------------------------------------------------------------


class TestRouteResultType:
    def test_route_returns_route_result(self):
        router = _make_router()
        result = router.route("file.pdf")
        assert isinstance(result, RouteResult)

    def test_route_result_has_collection_and_embedding(self):
        router = _make_router()
        result = router.route("file.pdf")
        assert result is not None
        assert hasattr(result, "collection")
        assert hasattr(result, "embedding")

    def test_default_embedding_used_when_no_rule_matches(self):
        router = Router(rules=[], default_collection="default", default_embedding="docs")
        result = _route_expect(router, "file.pdf")
        assert result.collection == "default"
        assert result.embedding == "docs"

    def test_rule_embedding_field_used_when_set(self):
        rules = [
            RoutingRule(
                collection="vista-source",
                file_group=["general-source"],
                embedding="code",
            )
        ]
        router = Router(rules=rules, default_collection="default", default_embedding="docs")
        result = _route_expect(router, "src/main.py", file_type_group="general-source")
        assert result.collection == "vista-source"
        assert result.embedding == "code"

    def test_rule_uses_default_embedding_when_rule_embedding_empty(self):
        rules = [
            RoutingRule(
                collection="vista",
                file_group=["office-documents"],
                embedding="",
            )
        ]
        router = Router(rules=rules, default_collection="default", default_embedding="docs")
        result = _route_expect(router, "doc.pdf", file_type_group="office-documents")
        assert result.collection == "vista"
        assert result.embedding == "docs"

    def test_multiple_rules_with_different_embeddings(self):
        rules = [
            RoutingRule(collection="src-col", file_group=["general-source"], embedding="code"),
            RoutingRule(collection="doc-col", file_group=["office-documents"], embedding="docs"),
        ]
        router = Router(rules=rules, default_collection="default", default_embedding="docs")

        code_result = _route_expect(router, "main.py", file_type_group="general-source")
        assert code_result.collection == "src-col"
        assert code_result.embedding == "code"

        doc_result = _route_expect(router, "report.pdf", file_type_group="office-documents")
        assert doc_result.collection == "doc-col"
        assert doc_result.embedding == "docs"


# ---------------------------------------------------------------------------
# Skip rule tests
# ---------------------------------------------------------------------------


class TestSkipRules:
    """Tests for routing skip rules (route returns None)."""

    def test_skip_rule_returns_none(self):
        rules = [RoutingRule(skip=True, path=["junk/"])]
        router = _make_router(rules)
        assert router.route("junk/file.html") is None

    def test_skip_rule_non_matching_falls_through(self):
        rules = [RoutingRule(skip=True, path=["junk/"])]
        router = _make_router(rules)
        result = _route_expect(router, "docs/file.pdf")
        assert result.collection == "default"

    def test_skip_before_collection_rule(self):
        """Skip rule takes precedence via first-match-wins."""
        rules = [
            RoutingRule(skip=True, path=["reports/"]),
            RoutingRule(collection="docs", file_group=["office-documents"]),
        ]
        router = _make_router(rules)
        # Matches skip rule first
        assert router.route("reports/doc.pdf", file_type_group="office-documents") is None
        # Does not match skip, matches collection rule
        result = _route_expect(router, "other/doc.pdf", file_type_group="office-documents")
        assert result.collection == "docs"

    def test_skip_with_file_group(self):
        rules = [RoutingRule(skip=True, file_group=["web-content"])]
        router = _make_router(rules)
        assert router.route("page.html", file_type_group="web-content") is None
        result = router.route("page.html", file_type_group="office-documents")
        assert result is not None

    def test_skip_with_filename_glob(self):
        rules = [RoutingRule(skip=True, filename=["FileComparisonReport*"])]
        router = _make_router(rules)
        assert router.route("dir/FileComparisonReport_123.html") is None
        result = router.route("dir/readme.html")
        assert result is not None

    def test_skip_with_path_and_file_group_and(self):
        """Skip rule with multiple criteria uses AND semantics."""
        rules = [
            RoutingRule(
                skip=True,
                path=["FolderComparisonReport_files/"],
                file_group=["web-content"],
            )
        ]
        router = _make_router(rules)
        # Both match → skip
        result = router.route("FolderComparisonReport_files/f.html", file_type_group="web-content")
        assert result is None
        # Only path matches → no skip
        result = router.route("FolderComparisonReport_files/f.m", file_type_group="mumps")
        assert result is not None

    def test_skip_with_multiple_paths_or(self):
        """Multiple path values are ORed within a single skip rule."""
        rules = [
            RoutingRule(
                skip=True,
                path=["FolderComparisonReport_files/", "Public_Report_files/"],
            )
        ]
        router = _make_router(rules)
        assert router.route("FolderComparisonReport_files/x.html") is None
        assert router.route("Public_Report_files/y.html") is None
        result = router.route("real_docs/z.pdf")
        assert result is not None
