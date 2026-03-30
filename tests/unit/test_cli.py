"""Unit tests for CLI module."""

from __future__ import annotations

import json
import textwrap

import pytest

from thresher.cli import main


class TestCLIArgParsing:
    """Tests for CLI argument parsing."""

    def test_controller_subcommand(self, monkeypatch):
        """Controller subcommand should be accepted."""

        def mock_scan(source, config):
            return [], []

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_direct_files",
            mock_scan,
        )
        monkeypatch.setattr(
            "thresher.runner.processor.create_source_provider",
            mock_create_source,
        )

        result = main(["controller", "--dry-run"])
        assert result == 0

    def test_runner_subcommand_requires_runner_id(self):
        """Runner subcommand without --runner-id should fail."""
        with pytest.raises(SystemExit) as exc_info:
            main(["runner"])
        assert exc_info.value.code == 2

    def test_no_subcommand_fails(self):
        """Running without a subcommand should fail."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_unknown_subcommand_fails(self):
        """Running with an unknown subcommand should fail."""
        with pytest.raises(SystemExit) as exc_info:
            main(["unknown"])
        assert exc_info.value.code == 2

    def test_controller_dry_run(self, monkeypatch):
        """Controller --dry-run calls scan but not build queue."""
        scan_called = []
        build_called = []

        def mock_scan_files(source, config):
            scan_called.append(True)
            return [
                {
                    "path": "file.m",
                    "source_type": "direct",
                    "file_type_group": "mumps",
                }
            ], []

        def mock_build_queue(items, source, **kwargs):
            build_called.append(True)
            return ["batch-0001"]

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_direct_files",
            mock_scan_files,
        )
        monkeypatch.setattr(
            "thresher.controller.queue_builder.build_queue",
            mock_build_queue,
        )
        monkeypatch.setattr(
            "thresher.runner.processor.create_source_provider",
            mock_create_source,
        )

        result = main(["controller", "--dry-run"])

        assert result == 0
        assert len(scan_called) == 1
        assert len(build_called) == 0

    def test_controller_log_level(self, monkeypatch):
        """Controller should accept --log-level flag."""

        def mock_scan(source, config):
            return [], []

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_direct_files",
            mock_scan,
        )
        monkeypatch.setattr(
            "thresher.runner.processor.create_source_provider",
            mock_create_source,
        )

        result = main(
            [
                "--log-level",
                "DEBUG",
                "controller",
                "--dry-run",
            ]
        )
        assert result == 0

    def test_controller_config_flag(self, monkeypatch):
        """Controller should accept --config flag."""

        def mock_scan(source, config):
            return [], []

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_direct_files",
            mock_scan,
        )
        monkeypatch.setattr(
            "thresher.runner.processor.create_source_provider",
            mock_create_source,
        )

        result = main(
            [
                "--config",
                "nonexistent.yaml",
                "controller",
                "--dry-run",
            ]
        )
        assert result == 0


# ---------------------------------------------------------------------------
# mcp-config subcommand tests (T024)
# ---------------------------------------------------------------------------


class TestMcpConfigSubcommand:
    """Tests for the `thresher mcp-config` CLI subcommand."""

    def test_mcp_config_outputs_valid_json(self, capsys, tmp_path):
        """mcp-config outputs valid JSON to stdout."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""
                embedding:
                  default: docs
                  models:
                    docs:
                      model: "nomic-ai/nomic-embed-text-v1.5"
                      vector_size: 768
                      vector_name: "nomic-v1.5"
                      query_prefix: "search_query: "
                    code:
                      model: "jinaai/jina-embeddings-v2-base-code"
                      vector_size: 768
                      vector_name: "jina-code-v2"
                routing:
                  default_collection: vista
                  rules:
                    - collection: vista-source
                      embedding: code
                    - collection: vista
            """),
            encoding="utf-8",
        )
        result = main(["--config", str(config_file), "mcp-config"])
        assert result == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "qdrant_url" in output
        assert "collections" in output
        assert output["default_collection"] == "vista"
        assert output["read_only"] is True

    def test_mcp_config_includes_all_collections(self, capsys, tmp_path):
        """mcp-config enumerates all collections from routing rules + default."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""
                embedding:
                  default: docs
                  models:
                    docs:
                      model: "nomic/text"
                      vector_size: 768
                      vector_name: "nomic"
                    code:
                      model: "jina/code"
                      vector_size: 768
                      vector_name: "jina"
                routing:
                  default_collection: vista
                  rules:
                    - collection: vista-source
                      embedding: code
                    - collection: rpms
                      embedding: docs
                    - collection: rpms-source
                      embedding: code
            """),
            encoding="utf-8",
        )
        result = main(["--config", str(config_file), "mcp-config"])
        assert result == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        collection_names = [c["name"] for c in output["collections"]]
        assert set(collection_names) == {"vista", "vista-source", "rpms", "rpms-source"}

    def test_mcp_config_correct_model_assignment(self, capsys, tmp_path):
        """Collections are assigned their correct embedding model configs."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""
                embedding:
                  default: docs
                  models:
                    docs:
                      model: "nomic-ai/nomic-embed-text-v1.5"
                      vector_size: 768
                      vector_name: "nomic-v1.5"
                      query_prefix: "search_query: "
                    code:
                      model: "jinaai/jina-embeddings-v2-base-code"
                      vector_size: 768
                      vector_name: "jina-code-v2"
                routing:
                  default_collection: vista
                  rules:
                    - collection: vista-source
                      embedding: code
            """),
            encoding="utf-8",
        )
        result = main(["--config", str(config_file), "mcp-config"])
        assert result == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        by_name = {c["name"]: c for c in output["collections"]}
        assert "vista" in by_name
        assert by_name["vista"]["model"] == "nomic-ai/nomic-embed-text-v1.5"
        assert by_name["vista"]["vector_name"] == "nomic-v1.5"
        assert by_name["vista"]["query_prefix"] == "search_query: "

        assert "vista-source" in by_name
        assert by_name["vista-source"]["model"] == "jinaai/jina-embeddings-v2-base-code"
        assert by_name["vista-source"]["vector_name"] == "jina-code-v2"
        assert by_name["vista-source"]["query_prefix"] == ""

    def test_mcp_config_legacy_single_model(self, capsys):
        """mcp-config works with legacy single-model config (backward compat)."""
        result = main(["mcp-config"])
        assert result == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "collections" in output
        # Legacy config has one default model → default collection gets it
        assert len(output["collections"]) >= 1
        assert output["collections"][0]["model"] == "sentence-transformers/all-MiniLM-L6-v2"
