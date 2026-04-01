"""Collection routing engine with configurable rules."""

from __future__ import annotations

import fnmatch
import logging
import re

from thresher.types import RouteResult, RoutingRule

logger = logging.getLogger("thresher.router")


class Router:
    """Routes files to destination collections based on configurable rules.

    Rules are evaluated in declaration order with first-match-wins semantics.
    Within a rule, criteria types are ANDed; values within each are ORed.
    """

    def __init__(
        self,
        rules: list[RoutingRule],
        default_collection: str = "default",
        default_embedding: str = "default",
    ):
        self.rules = rules
        self.default_collection = default_collection
        self.default_embedding = default_embedding

    def route(
        self,
        file_path: str,
        file_type_group: str | None = None,
    ) -> RouteResult | None:
        """Determine the target collection and embedding model for a file.

        Args:
            file_path: Source provider path to the file
            file_type_group: Classified file type group name

        Returns:
            RouteResult with collection name and embedding model name,
            or ``None`` if a skip rule matched (file should be excluded).
        """
        filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path

        for rule in self.rules:
            if self._matches_rule(rule, file_path, filename, file_type_group):
                if rule.skip:
                    logger.debug("Skip rule '%s' matched: %s", rule.name, file_path)
                    return None
                embedding = rule.embedding or self.default_embedding
                return RouteResult(collection=rule.collection, embedding=embedding)

        return RouteResult(collection=self.default_collection, embedding=self.default_embedding)

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
    """Match a path against a pattern (substring or regex).

    Regex patterns (starting with ``^`` or ending with ``$``) are matched
    case-insensitively.  Any inline ``(?i)`` flag is stripped before
    compilation so the pattern is valid on Python 3.13+ where global flags
    must appear at the very start of the expression.
    """
    if pattern.startswith("^") or pattern.endswith("$"):
        clean = re.sub(r"\(\?[aiLmsux]+\)", "", pattern)
        return bool(re.search(clean, file_path, re.IGNORECASE))
    return pattern.lower() in file_path.lower()
