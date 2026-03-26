"""Memory monitoring and Linux memory optimizations (T028, T032)."""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import resource

logger = logging.getLogger("thresher.runner.memory_monitor")


def check_memory(threshold_mb: int) -> bool:
    """Return True if current RSS exceeds *threshold_mb* megabytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is in KB on Linux, bytes on macOS
    if _is_macos():
        rss_mb = usage.ru_maxrss / (1024 * 1024)
    else:
        rss_mb = usage.ru_maxrss / 1024
    return rss_mb > threshold_mb


def apply_memory_optimizations(malloc_arena_max: int = 2) -> None:
    """Apply one-time Linux memory optimizations at process start.

    Sets MALLOC_ARENA_MAX, runs gc.collect, and attempts malloc_trim.
    """
    os.environ.setdefault("MALLOC_ARENA_MAX", str(malloc_arena_max))
    gc.collect()
    _try_malloc_trim()
    logger.info(
        "Memory optimizations applied: MALLOC_ARENA_MAX=%s",
        os.environ.get("MALLOC_ARENA_MAX"),
    )


def gc_between_files() -> None:
    """Lightweight GC pass intended to be called between file processing."""
    gc.collect()
    _try_malloc_trim()


# -- internal helpers -------------------------------------------------------


def _is_macos() -> bool:
    import sys

    return sys.platform == "darwin"


def _try_malloc_trim() -> None:
    """Call glibc malloc_trim(0) if available (Linux only)."""
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass
