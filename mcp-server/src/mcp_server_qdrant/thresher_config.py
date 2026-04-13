"""Read a thresher pipeline YAML config and extract MCP server collection mappings.

This module reads thresher YAML directly with pyyaml — it does NOT import the
thresher package, avoiding its heavy ML dependencies (docling, torch, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mcp_server_qdrant.settings import CollectionConfig


@dataclass
class ThresherMCPConfig:
    """Extracted MCP-relevant configuration from a thresher pipeline YAML."""

    qdrant_url: str
    qdrant_api_key: str
    default_collection: str
    collections: list[CollectionConfig]
    tool_find_description: str | None = None


def read_thresher_config(config_path: str | Path) -> ThresherMCPConfig:
    """Read a thresher YAML config and extract MCP server collection mappings.

    Walks ``embedding.models``, ``routing.rules``, and ``routing.default_collection``
    to build a list of ``CollectionConfig`` entries with the correct embedding model
    assigned to each collection.

    Args:
        config_path: Path to a thresher pipeline YAML config file.

    Returns:
        ThresherMCPConfig with Qdrant connection info and per-collection model mappings.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config is missing required embedding model definitions.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Extract Qdrant connection
    dest = raw.get("destination", {})
    qdrant = dest.get("qdrant", {}) if isinstance(dest, dict) else {}
    qdrant_url = str(qdrant.get("url", "http://localhost:6333"))
    qdrant_api_key = str(qdrant.get("api_key", ""))

    # Extract embedding models
    embed_raw = raw.get("embedding", {})
    if not isinstance(embed_raw, dict):
        embed_raw = {}

    default_model_name = str(embed_raw.get("default", "default"))
    raw_models = embed_raw.get("models", {})
    if not isinstance(raw_models, dict):
        raw_models = {}

    # Fallback: legacy single-model config
    if not raw_models:
        raw_models = {
            "default": {
                "model": str(embed_raw.get("model", "sentence-transformers/all-MiniLM-L6-v2")),
                "vector_size": int(embed_raw.get("vector_size", 384)),
                "vector_name": str(embed_raw.get("vector_name", "fast-all-minilm-l6-v2")),
                "max_tokens": int(embed_raw.get("max_tokens", 512)),
            }
        }
        default_model_name = "default"

    # Extract routing
    routing = raw.get("routing", {})
    if not isinstance(routing, dict):
        routing = {}
    default_collection = str(routing.get("default_collection", "default"))
    rules = routing.get("rules", [])
    if not isinstance(rules, list):
        rules = []

    # Build collection -> model_name mapping (first-match-wins for each collection)
    collection_models: dict[str, str] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        col = rule.get("collection", "")
        if col and col not in collection_models:
            model_name = rule.get("embedding", "") or default_model_name
            collection_models[col] = model_name

    # Add default collection if not already covered by rules
    if default_collection and default_collection not in collection_models:
        collection_models[default_collection] = default_model_name

    # Build CollectionConfig list
    collections: list[CollectionConfig] = []
    for col_name, model_name in collection_models.items():
        model_spec = raw_models.get(model_name)
        if model_spec is None or not isinstance(model_spec, dict):
            continue
        collections.append(
            CollectionConfig(
                name=col_name,
                model=str(model_spec.get("model", "")),
                vector_name=str(model_spec.get("vector_name", "")),
                vector_size=int(model_spec.get("vector_size", 384)),
                index_prefix=str(model_spec.get("index_prefix", "")),
                query_prefix=str(model_spec.get("query_prefix", "")),
            )
        )

    # Extract MCP-specific settings
    mcp_raw = raw.get("mcp", {})
    if not isinstance(mcp_raw, dict):
        mcp_raw = {}
    tool_find_description = mcp_raw.get("tool_find_description")

    return ThresherMCPConfig(
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key,
        default_collection=default_collection,
        collections=collections,
        tool_find_description=tool_find_description,
    )
