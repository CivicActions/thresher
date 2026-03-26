"""Unit tests for thresher.runner.memory_monitor (T028, T032)."""

from __future__ import annotations

from unittest.mock import patch

from thresher.runner.memory_monitor import (
    apply_memory_optimizations,
    check_memory,
    gc_between_files,
)


class TestCheckMemory:
    """Tests for check_memory."""

    def test_returns_false_when_below_threshold(self) -> None:
        # Use a very high threshold that real RSS can never exceed
        assert check_memory(threshold_mb=999_999) is False

    def test_returns_true_when_above_threshold(self) -> None:
        # Use a threshold of 0 MB — any running process exceeds this
        assert check_memory(threshold_mb=0) is True

    def test_returns_bool(self) -> None:
        result = check_memory(threshold_mb=4096)
        assert isinstance(result, bool)


class TestApplyMemoryOptimizations:
    """Tests for apply_memory_optimizations."""

    def test_runs_without_error(self) -> None:
        apply_memory_optimizations(malloc_arena_max=2)

    def test_sets_malloc_arena_max(self) -> None:
        import os

        # Clear the env var first so setdefault actually sets it
        os.environ.pop("MALLOC_ARENA_MAX", None)
        apply_memory_optimizations(malloc_arena_max=4)
        assert os.environ["MALLOC_ARENA_MAX"] == "4"


class TestGcBetweenFiles:
    """Tests for gc_between_files."""

    def test_runs_without_error(self) -> None:
        gc_between_files()

    def test_calls_gc_collect(self) -> None:
        with patch("thresher.runner.memory_monitor.gc.collect") as mock_gc:
            gc_between_files()
            mock_gc.assert_called_once()
