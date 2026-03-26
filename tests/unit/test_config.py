"""Unit tests for thresher.config — three-layer config loading."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

from thresher.config import (
    Config,
    _deep_get,
    _deep_set,
    _merge_configs,
    _parse_file_type_groups,
    _parse_routing_rules,
    load_config,
)
from thresher.types import FileTypeGroup, RoutingRule

# ---------------------------------------------------------------------------
# Defaults-only loading
# ---------------------------------------------------------------------------


class TestLoadDefaults:
    """load_config() with no user config should return built-in defaults."""

    def test_returns_config_instance(self):
        cfg = load_config()
        assert isinstance(cfg, Config)

    def test_contains_expected_file_type_groups(self):
        cfg = load_config()
        expected = {
            "office-documents",
            "general-source",
            "data-files",
            "images",
            "plain-text",
            "binary",
        }
        assert expected == set(cfg.file_type_groups.keys())

    def test_office_documents_group(self):
        cfg = load_config()
        grp = cfg.file_type_groups["office-documents"]
        assert isinstance(grp, FileTypeGroup)
        assert ".pdf" in grp.extensions
        assert grp.extractor == "docling"
        assert grp.chunker.strategy == "docling-hybrid"
        assert grp.priority == 50

    def test_general_source_group(self):
        cfg = load_config()
        grp = cfg.file_type_groups["general-source"]
        assert ".py" in grp.extensions
        assert ".hs" in grp.extensions
        assert grp.chunker.strategy == "chonkie-code"
        assert grp.chunker.language == "auto"
        assert grp.priority == 60

    def test_binary_group(self):
        cfg = load_config()
        grp = cfg.file_type_groups["binary"]
        assert grp.extractor == "skip"
        assert grp.chunker.strategy == "skip"
        assert grp.priority == 999

    def test_general_source_detectors(self):
        cfg = load_config()
        grp = cfg.file_type_groups["general-source"]
        assert grp.detectors == []

    def test_plain_text_recipe(self):
        cfg = load_config()
        grp = cfg.file_type_groups["plain-text"]
        assert grp.chunker.recipe == "markdown"

    def test_default_qdrant_url(self):
        cfg = load_config()
        assert cfg.destination.qdrant.url == "http://localhost:6333"

    def test_default_routing(self):
        cfg = load_config()
        assert cfg.routing.default_collection == "default"
        assert cfg.routing.rules == []


# ---------------------------------------------------------------------------
# User config overrides
# ---------------------------------------------------------------------------


class TestUserConfigOverride:
    """User YAML should merge with / override defaults."""

    def _write_user_config(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "user.yaml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_user_replaces_file_type_group(self, tmp_path):
        user_file = self._write_user_config(
            tmp_path,
            """\
            file_type_groups:
              office-documents:
                extensions: [".pdf"]
                extractor: custom-extractor
                chunker:
                  strategy: custom-strategy
                  chunk_size: 1024
                priority: 10
        """,
        )
        cfg = load_config(user_file)
        grp = cfg.file_type_groups["office-documents"]
        assert grp.extensions == [".pdf"]
        assert grp.extractor == "custom-extractor"
        assert grp.chunker.strategy == "custom-strategy"
        assert grp.chunker.chunk_size == 1024
        assert grp.priority == 10

    def test_user_adds_new_file_type_group(self, tmp_path):
        user_file = self._write_user_config(
            tmp_path,
            """\
            file_type_groups:
              custom-group:
                extensions: [".xyz"]
                extractor: custom
                chunker:
                  strategy: custom
                priority: 5
        """,
        )
        cfg = load_config(user_file)
        assert "custom-group" in cfg.file_type_groups
        # Built-in groups still present
        assert "office-documents" in cfg.file_type_groups

    def test_merge_preserves_unmentioned_defaults(self, tmp_path):
        user_file = self._write_user_config(
            tmp_path,
            """\
            processing:
              docling_timeout: 999
        """,
        )
        cfg = load_config(user_file)
        assert cfg.processing.docling_timeout == 999
        # Unmentioned defaults preserved
        assert len(cfg.file_type_groups) == 6
        assert cfg.destination.qdrant.url == "http://localhost:6333"

    def test_nonexistent_user_path_ignored(self):
        cfg = load_config("/nonexistent/path/config.yaml")
        assert isinstance(cfg, Config)
        assert len(cfg.file_type_groups) == 6


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


class TestEnvVarOverrides:
    def test_qdrant_url_override(self):
        with patch.dict("os.environ", {"QDRANT_URL": "http://remote:6333"}):
            cfg = load_config()
        assert cfg.destination.qdrant.url == "http://remote:6333"

    def test_qdrant_api_key_override(self):
        with patch.dict("os.environ", {"QDRANT_API_KEY": "secret-key"}):
            cfg = load_config()
        assert cfg.destination.qdrant.api_key == "secret-key"

    def test_gcs_bucket_override(self):
        with patch.dict("os.environ", {"GCS_BUCKET": "my-bucket"}):
            cfg = load_config()
        assert cfg.source.gcs.bucket == "my-bucket"

    def test_env_overrides_user_config(self, tmp_path):
        user_file = tmp_path / "user.yaml"
        user_file.write_text(
            "destination:\n  qdrant:\n    url: http://user:6333\n",
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"QDRANT_URL": "http://env-wins:6333"}):
            cfg = load_config(user_file)
        assert cfg.destination.qdrant.url == "http://env-wins:6333"


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


class TestMergeConfigs:
    def test_user_none_values_use_defaults(self):
        defaults = {"a": {"x": 1, "y": 2}}
        user = {}
        merged = _merge_configs(defaults, user)
        assert merged["a"] == {"x": 1, "y": 2}

    def test_file_type_groups_merge_by_name(self):
        defaults = {
            "file_type_groups": {
                "a": {"extensions": [".a"], "priority": 1},
                "b": {"extensions": [".b"], "priority": 2},
            }
        }
        user = {
            "file_type_groups": {
                "b": {"extensions": [".b2"], "priority": 20},
                "c": {"extensions": [".c"], "priority": 3},
            }
        }
        merged = _merge_configs(defaults, user)
        groups = merged["file_type_groups"]
        assert groups["a"] == {"extensions": [".a"], "priority": 1}
        assert groups["b"] == {"extensions": [".b2"], "priority": 20}
        assert groups["c"] == {"extensions": [".c"], "priority": 3}

    def test_scalar_override(self):
        defaults = {"force": False}
        user = {"force": True}
        merged = _merge_configs(defaults, user)
        assert merged["force"] is True


# ---------------------------------------------------------------------------
# Routing rule parsing
# ---------------------------------------------------------------------------


class TestRoutingRuleParsing:
    def test_empty_rules(self):
        assert _parse_routing_rules(None) == []
        assert _parse_routing_rules([]) == []

    def test_single_rule(self):
        raw = [
            {
                "collection": "mumps",
                "name": "MUMPS files",
                "file_group": ["mumps-source", "mumps-globals"],
                "path": ["Packages/"],
            }
        ]
        rules = _parse_routing_rules(raw)
        assert len(rules) == 1
        assert isinstance(rules[0], RoutingRule)
        assert rules[0].collection == "mumps"
        assert rules[0].file_group == ["mumps-source", "mumps-globals"]
        assert rules[0].path == ["Packages/"]

    def test_non_dict_entries_skipped(self):
        raw = [{"collection": "ok"}, "bad-entry", 42]
        rules = _parse_routing_rules(raw)
        assert len(rules) == 1

    def test_routing_rules_from_user_config(self, tmp_path):
        user_file = tmp_path / "user.yaml"
        user_file.write_text(
            textwrap.dedent("""\
            routing:
              default_collection: my-collection
              rules:
                - collection: mumps
                  name: MUMPS files
                  file_group: [mumps-source]
                  path: [Packages/]
        """),
            encoding="utf-8",
        )
        cfg = load_config(user_file)
        assert cfg.routing.default_collection == "my-collection"
        assert len(cfg.routing.rules) == 1
        assert cfg.routing.rules[0].collection == "mumps"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestDeepGetSet:
    def test_deep_get(self):
        d = {"a": {"b": {"c": 42}}}
        assert _deep_get(d, "a.b.c") == 42

    def test_deep_get_missing(self):
        d = {"a": {"b": 1}}
        assert _deep_get(d, "a.x.y") is None

    def test_deep_set_creates_intermediates(self):
        d: dict = {}
        _deep_set(d, "a.b.c", "val")
        assert d == {"a": {"b": {"c": "val"}}}


# ---------------------------------------------------------------------------
# Parse file type groups
# ---------------------------------------------------------------------------


class TestParseFileTypeGroups:
    def test_empty_input(self):
        assert _parse_file_type_groups(None) == {}
        assert _parse_file_type_groups({}) == {}

    def test_non_dict_spec_skipped(self):
        result = _parse_file_type_groups({"bad": "not-a-dict"})
        assert result == {}

    def test_minimal_group(self):
        raw = {
            "test-group": {
                "extensions": [".test"],
                "extractor": "raw-text",
                "chunker": {"strategy": "test-strat"},
            }
        }
        result = _parse_file_type_groups(raw)
        assert "test-group" in result
        grp = result["test-group"]
        assert grp.name == "test-group"
        assert grp.extensions == [".test"]
        assert grp.chunker.strategy == "test-strat"
        assert grp.priority == 100  # default
