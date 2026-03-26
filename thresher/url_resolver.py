"""URL resolution for source files — pluggable resolver chain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_HTTRACK_MIRROR_RE = re.compile(r"<!--\s*Mirrored from\s+(\S+)")


@dataclass
class UrlResolverConfig:
    """Configuration for a single URL resolver in the chain."""

    type: str  # "httrack", "pattern", "domain-first"
    match: str = ""  # regex pattern (for "pattern" type)
    template: str = ""  # URL template with {1}, {2} group refs (for "pattern" type)
    strip_prefix: str = ""  # prefix to strip before matching


def resolve_source_url(
    source_path: str,
    content: str | None = None,
    resolvers: list[UrlResolverConfig] | None = None,
) -> str:
    """Resolve a source URL using a chain of configured resolvers.

    Resolvers are tried in order; first non-None result wins.
    If no resolvers are configured, uses default chain: httrack → domain-first.
    """
    if resolvers is None:
        resolvers = _default_resolvers()

    for resolver in resolvers:
        result = _apply_resolver(resolver, source_path, content)
        if result is not None:
            return result

    # Ultimate fallback: return path as-is
    return source_path


def _default_resolvers() -> list[UrlResolverConfig]:
    """Default resolver chain when none configured."""
    return [
        UrlResolverConfig(type="httrack"),
        UrlResolverConfig(type="domain-first", strip_prefix="source/"),
    ]


def _apply_resolver(
    resolver: UrlResolverConfig,
    source_path: str,
    content: str | None,
) -> str | None:
    """Apply a single resolver. Returns URL or None if no match."""
    if resolver.type == "httrack":
        return _resolve_httrack(content)
    elif resolver.type == "pattern":
        return _resolve_pattern(source_path, resolver)
    elif resolver.type == "domain-first":
        return _resolve_domain_first(source_path, resolver.strip_prefix)
    return None


def _resolve_httrack(content: str | None) -> str | None:
    """Extract URL from httrack mirror comment in HTML content."""
    if not content:
        return None
    match = _HTTRACK_MIRROR_RE.search(content[:4096])
    if not match:
        return None
    url = match.group(1)
    if not url.startswith("http"):
        url = f"https://{url}"
    return url


def _resolve_pattern(source_path: str, resolver: UrlResolverConfig) -> str | None:
    """Match path against regex and format URL from template."""
    path = source_path
    if resolver.strip_prefix and path.startswith(resolver.strip_prefix):
        path = path[len(resolver.strip_prefix) :]

    if not resolver.match:
        return None

    m = re.search(resolver.match, path)
    if not m:
        return None

    # Substitute {1}, {2}, etc. with captured groups
    url = resolver.template
    for i, group in enumerate(m.groups(), 1):
        url = url.replace(f"{{{i}}}", group or "")
    return url


def _resolve_domain_first(source_path: str, strip_prefix: str = "") -> str:
    """Reconstruct URL assuming first path component is a domain."""
    path = source_path
    if strip_prefix and path.startswith(strip_prefix):
        path = path[len(strip_prefix) :]

    parts = path.split("/", 1)
    domain = parts[0]
    remaining = parts[1] if len(parts) > 1 else ""
    if remaining:
        return f"https://{domain}/{remaining}"
    return f"https://{domain}"


def parse_url_resolvers(raw: list[Any] | None) -> list[UrlResolverConfig]:
    """Parse raw url_resolvers config list into UrlResolverConfig objects."""
    resolvers: list[UrlResolverConfig] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        resolvers.append(
            UrlResolverConfig(
                type=entry.get("type", ""),
                match=entry.get("match", ""),
                template=entry.get("template", ""),
                strip_prefix=entry.get("strip_prefix", ""),
            )
        )
    return resolvers
