"""Unit tests for CLI module."""

from __future__ import annotations

import pytest

from thresher.cli import main


class TestCLIArgParsing:
    """Tests for CLI argument parsing."""

    def test_controller_subcommand(self, monkeypatch):
        """Controller subcommand should be accepted."""

        def mock_scan(source, config):
            return []

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_files",
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
            ]

        def mock_build_queue(items, source, **kwargs):
            build_called.append(True)
            return ["batch-0001"]

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_files",
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
            return []

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_files",
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
            return []

        def mock_create_source(config):
            from unittest.mock import MagicMock

            return MagicMock()

        monkeypatch.setattr(
            "thresher.controller.scanner.scan_files",
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
