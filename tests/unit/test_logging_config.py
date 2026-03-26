"""Tests for thresher.logging_config."""

from __future__ import annotations

import json
import logging

from thresher.logging_config import StructuredFormatter, get_logger, setup_logging


class TestSetupLogging:
    """Tests for setup_logging."""

    def test_creates_handler_on_thresher_logger(self) -> None:
        setup_logging(level="DEBUG", json_output=True)
        thresher_logger = logging.getLogger("thresher")
        assert len(thresher_logger.handlers) == 1
        assert thresher_logger.level == logging.DEBUG

    def test_replaces_existing_handlers(self) -> None:
        thresher_logger = logging.getLogger("thresher")
        thresher_logger.addHandler(logging.StreamHandler())
        thresher_logger.addHandler(logging.StreamHandler())
        setup_logging()
        assert len(thresher_logger.handlers) == 1

    def test_human_readable_format(self) -> None:
        setup_logging(level="INFO", json_output=False)
        thresher_logger = logging.getLogger("thresher")
        handler = thresher_logger.handlers[0]
        assert not isinstance(handler.formatter, StructuredFormatter)


class TestStructuredFormatter:
    """Tests for StructuredFormatter."""

    def test_produces_valid_json(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="thresher.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "thresher.test"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed

    def test_extra_fields_included(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="thresher.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Processing file",
            args=None,
            exc_info=None,
        )
        record.file_path = "/some/file.pdf"  # type: ignore[attr-defined]
        record.duration_seconds = 1.5  # type: ignore[attr-defined]
        record.status = "success"  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["file_path"] == "/some/file.pdf"
        assert parsed["duration_seconds"] == 1.5
        assert parsed["status"] == "success"

    def test_exception_info_included(self) -> None:
        formatter = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="thresher.test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Something failed",
            args=None,
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestGetLogger:
    """Tests for get_logger."""

    def test_returns_logger_under_thresher_namespace(self) -> None:
        log = get_logger("mymodule")
        assert log.name == "thresher.mymodule"

    def test_returns_logging_logger_instance(self) -> None:
        log = get_logger("foo")
        assert isinstance(log, logging.Logger)
