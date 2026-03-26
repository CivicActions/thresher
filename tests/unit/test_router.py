"""Tests for thresher.processing.router."""

from __future__ import annotations

from thresher.processing.router import Router, _path_matches
from thresher.types import RoutingRule


def _make_router(rules: list[RoutingRule] | None = None) -> Router:
    return Router(
        rules=rules or [],
        default_collection="vista",
    )


class TestDefaultRouting:
    def test_no_rules_returns_default(self):
        router = _make_router()
        assert router.route("docs/file.pdf") == "vista"

    def test_source_routing_via_rule(self):
        rules = [
            RoutingRule(
                collection="vista-source",
                file_group=["mumps-source", "general-source"],
            )
        ]
        router = _make_router(rules)
        assert router.route("src/main.py", file_type_group="general-source") == "vista-source"

    def test_no_source_rule_uses_default(self):
        router = _make_router()
        assert router.route("src/main.py", file_type_group="general-source") == "vista"

    def test_custom_default_collection(self):
        router = Router(rules=[], default_collection="custom")
        assert router.route("file.txt") == "custom"


class TestFirstMatchWins:
    def test_first_matching_rule_wins(self):
        rules = [
            RoutingRule(collection="docs-collection", file_group=["office-documents"]),
            RoutingRule(collection="catch-all", file_group=["office-documents"]),
        ]
        router = _make_router(rules)
        result = router.route("report.pdf", file_type_group="office-documents")
        assert result == "docs-collection"

    def test_non_matching_rule_skipped(self):
        rules = [
            RoutingRule(collection="code-coll", file_group=["source-code"]),
            RoutingRule(collection="docs-coll", file_group=["office-documents"]),
        ]
        router = _make_router(rules)
        result = router.route("report.pdf", file_type_group="office-documents")
        assert result == "docs-coll"


class TestFileGroupCriterion:
    def test_file_group_match(self):
        rules = [RoutingRule(collection="target", file_group=["web-content", "office-documents"])]
        router = _make_router(rules)
        assert router.route("page.html", file_type_group="web-content") == "target"

    def test_file_group_no_match(self):
        rules = [RoutingRule(collection="target", file_group=["web-content"])]
        router = _make_router(rules)
        assert router.route("main.py", file_type_group="source-code") == "vista"

    def test_file_group_none_type(self):
        rules = [RoutingRule(collection="target", file_group=["web-content"])]
        router = _make_router(rules)
        assert router.route("unknown.xyz", file_type_group=None) == "vista"


class TestPathCriterion:
    def test_path_substring_match(self):
        rules = [RoutingRule(collection="legacy", path=["legacy/"])]
        router = _make_router(rules)
        assert router.route("legacy/old_doc.pdf") == "legacy"

    def test_path_no_match(self):
        rules = [RoutingRule(collection="legacy", path=["legacy/"])]
        router = _make_router(rules)
        assert router.route("current/new_doc.pdf") == "vista"

    def test_path_regex_match(self):
        rules = [RoutingRule(collection="versioned", path=[r"^v\d+/"])]
        router = _make_router(rules)
        assert router.route("v2/release-notes.md") == "versioned"

    def test_path_regex_no_match(self):
        rules = [RoutingRule(collection="versioned", path=[r"^v\d+/"])]
        router = _make_router(rules)
        assert router.route("docs/v2-notes.md") == "vista"

    def test_path_or_semantics(self):
        rules = [RoutingRule(collection="archive", path=["old/", "archive/"])]
        router = _make_router(rules)
        assert router.route("old/doc.pdf") == "archive"
        assert router.route("archive/doc.pdf") == "archive"
        assert router.route("new/doc.pdf") == "vista"


class TestFilenameCriterion:
    def test_filename_glob_match(self):
        rules = [RoutingRule(collection="readmes", filename=["README*"])]
        router = _make_router(rules)
        assert router.route("project/README.md") == "readmes"

    def test_filename_exact_match(self):
        rules = [RoutingRule(collection="licenses", filename=["LICENSE"])]
        router = _make_router(rules)
        assert router.route("project/LICENSE") == "licenses"

    def test_filename_no_match(self):
        rules = [RoutingRule(collection="readmes", filename=["README*"])]
        router = _make_router(rules)
        assert router.route("project/main.py") == "vista"

    def test_filename_or_semantics(self):
        rules = [RoutingRule(collection="meta", filename=["README*", "LICENSE*", "CHANGELOG*"])]
        router = _make_router(rules)
        assert router.route("p/README.md") == "meta"
        assert router.route("p/LICENSE") == "meta"
        assert router.route("p/CHANGELOG.md") == "meta"


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
        assert router.route("important/report.pdf", file_type_group="office-documents") == "special"
        # Only path matches
        assert router.route("important/main.py", file_type_group="source-code") == "vista"
        # Only group matches
        assert router.route("other/report.pdf", file_type_group="office-documents") == "vista"

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
        assert router.route("public/index.html", file_type_group="web-content") == "precise"
        assert router.route("public/about.html", file_type_group="web-content") == "vista"

    def test_empty_rule_never_matches(self):
        """A rule with no criteria should not match anything."""
        rules = [RoutingRule(collection="empty")]
        router = _make_router(rules)
        assert router.route("anything.txt") == "vista"


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
