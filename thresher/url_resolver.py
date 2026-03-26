"""URL resolution for source files — reconstructs original URLs from GCS paths."""

from __future__ import annotations

import re

_HTTRACK_MIRROR_RE = re.compile(r"<!--\s*Mirrored from\s+(\S+)")


def resolve_source_url(source_path: str, content: str | None = None) -> str:
    """Reconstruct the original URL for a source file.

    Priority:
    1. httrack "Mirrored from" comment in HTML content
    2. WorldVistA GitHub repository mapping
    3. Domain-first path reconstruction (fallback)
    """
    # Strip leading "source/" prefix
    relative = source_path
    if relative.startswith("source/"):
        relative = relative[len("source/") :]

    # 1. Try httrack comment extraction
    if content:
        match = _HTTRACK_MIRROR_RE.search(content[:4096])
        if match:
            url = match.group(1)
            if not url.startswith("http"):
                url = f"https://{url}"
            return url

    # 2. Try WorldVistA GitHub mapping
    if relative.startswith("WorldVistA/"):
        parts = relative.split("/", 2)
        if len(parts) >= 3:
            repo = parts[1]
            remaining = parts[2]
            return f"https://github.com/WorldVistA/{repo}/blob/HEAD/{remaining}"
        elif len(parts) == 2:
            return f"https://github.com/WorldVistA/{parts[1]}"

    # 3. Domain-first reconstruction (fallback)
    parts = relative.split("/", 1)
    domain = parts[0]
    remaining = parts[1] if len(parts) > 1 else ""
    if remaining:
        return f"https://{domain}/{remaining}"
    return f"https://{domain}"
