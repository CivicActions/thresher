"""Collection routing engine with configurable rules."""

from __future__ import annotations

import fnmatch
import logging
import re

from thresher.types import RoutingRule

logger = logging.getLogger("thresher.router")


class Router:
    """Routes files to destination collections based on configurable rules.

    Rules are evaluated in declaration order with first-match-wins semantics.
    Within a rule, criteria types are ANDed; values within each are ORed.
    """

    def __init__(
        self,
        rules: list[RoutingRule],
        default_collection: str = "vista",
        source_suffix: str = "-source",
    ):
        self.rules = rules
        self.default_collection = default_collection
        self.source_suffix = source_suffix

    def route(
        self,
        file_path: str,
        file_type_group: str | None = None,
        is_source: bool = False,
    ) -> str:
        """Determine the target collection for a file.

        Args:
            file_path: Source provider path to the file
            file_type_group: Classified file type group name
            is_source: Whether this is a source code file type group

        Returns:
            Target collection name
        """
        filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path

        for rule in self.rules:
            if self._matches_rule(rule, file_path, filename, file_type_group):
                return rule.collection

        # Default routing: source code groups get source suffix
        if is_source and self.source_suffix:
            return f"{self.default_collection}{self.source_suffix}"

        return self.default_collection

    def _matches_rule(
        self,
        rule: RoutingRule,
        file_path: str,
        filename: str,
        file_type_group: str | None,
    ) -> bool:
        """Check if a file matches a routing rule. Criteria are ANDed."""
        checks: list[bool] = []

        # File group criterion (ORed within)
        if rule.file_group:
            checks.append(file_type_group is not None and file_type_group in rule.file_group)

        # Path criterion (ORed within)
        if rule.path:
            path_match = any(_path_matches(file_path, pattern) for pattern in rule.path)
            checks.append(path_match)

        # Filename criterion (ORed within)
        if rule.filename:
            fn_match = any(fnmatch.fnmatch(filename, pattern) for pattern in rule.filename)
            checks.append(fn_match)

        # All criteria must match (AND), and at least one must be specified
        return bool(checks) and all(checks)


def _path_matches(file_path: str, pattern: str) -> bool:
    """Match a path against a pattern (substring or regex)."""
    if pattern.startswith("^") or pattern.endswith("$"):
        return bool(re.search(pattern, file_path))
    return pattern in file_path
