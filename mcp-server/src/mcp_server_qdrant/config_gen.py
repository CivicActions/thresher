"""Generate MCP client configuration for multiple IDE targets.

Supports VS Code, Claude Desktop, Cursor, and Claude Code. Each target gets the
appropriate JSON structure for stdio (local) or HTTP (remote/K8s) deployment.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from mcp_server_qdrant.settings import CollectionConfig


def _find_server_command() -> str:
    """Determine the best command to launch the MCP server.

    Returns 'mcp-server-qdrant' if it's on PATH, otherwise falls back to 'uvx'.
    """
    if shutil.which("mcp-server-qdrant"):
        return "mcp-server-qdrant"
    return "uvx"


def _build_stdio_server(
    collections: list[CollectionConfig],
    default_collection: str,
    qdrant_url: str,
    config_path: str | None,
    name: str,
) -> dict:
    """Build a stdio server config dict (command/args/env).

    If config_path is provided, the server is launched with ``--config <path>``.
    Otherwise, configuration is passed via environment variables.
    """
    cmd = _find_server_command()

    if cmd == "mcp-server-qdrant":
        args = []
        if config_path:
            args = ["--config", str(Path(config_path).resolve())]
    else:
        # uvx mode
        args = ["mcp-server-qdrant"]
        if config_path:
            args.extend(["--config", str(Path(config_path).resolve())])

    env: dict[str, str] = {}

    if not config_path:
        # Pass config via env vars when no config file
        env["QDRANT_URL"] = qdrant_url
        env["DEFAULT_COLLECTION"] = default_collection
        env["QDRANT_READ_ONLY"] = "true"
        env["COLLECTIONS"] = json.dumps([c.model_dump(exclude_defaults=False) for c in collections])

    return {
        "command": cmd,
        "args": args,
        "env": env,
    }


def _build_http_server(url: str) -> dict:
    """Build an HTTP server config dict for remote MCP server."""
    return {
        "url": url,
        "headers": {
            "Authorization": "Bearer ${input:mcpApiKey}",
        },
    }


def _qdrant_api_key_input() -> dict:
    """Standard input definition for the Qdrant API key."""
    return {
        "id": "qdrantApiKey",
        "type": "promptString",
        "description": "Qdrant API key (use a read-only key scoped to the search collections)",
        "password": True,
    }


def _mcp_api_key_input() -> dict:
    """Standard input definition for the MCP server bearer token."""
    return {
        "id": "mcpApiKey",
        "type": "promptString",
        "description": "MCP server API key (bearer token for remote access)",
        "password": True,
    }


def generate_vscode(
    collections: list[CollectionConfig],
    default_collection: str,
    qdrant_url: str,
    name: str = "vistaRpms",
    url: str | None = None,
    config_path: str | None = None,
) -> str:
    """Generate VS Code mcp.json configuration.

    VS Code uses ``{ "servers": { ... }, "inputs": [...] }`` format.

    Args:
        collections: Per-collection embedding model configs.
        default_collection: Default collection name.
        qdrant_url: Qdrant server URL.
        name: Server name (camelCase recommended).
        url: Remote MCP server URL. If set, generates HTTP config instead of stdio.
        config_path: Path to MCP server JSON config file (for stdio mode).

    Returns:
        JSON string of the VS Code mcp.json fragment.
    """
    inputs = []

    if url:
        server = {"type": "http", **_build_http_server(url)}
        inputs.append(_mcp_api_key_input())
    else:
        server = {
            "type": "stdio",
            **_build_stdio_server(collections, default_collection, qdrant_url, config_path, name),
        }
        if not config_path:
            server["env"]["QDRANT_API_KEY"] = "${input:qdrantApiKey}"
            inputs.append(_qdrant_api_key_input())

    output: dict[str, Any] = {"servers": {name: server}}
    if inputs:
        output["inputs"] = inputs

    return json.dumps(output, indent=2)


def generate_claude_desktop(
    collections: list[CollectionConfig],
    default_collection: str,
    qdrant_url: str,
    name: str = "vista-rpms",
    url: str | None = None,
    config_path: str | None = None,
) -> str:
    """Generate Claude Desktop configuration.

    Claude Desktop uses ``{ "mcpServers": { ... } }`` format.
    """
    if url:
        server = {"type": "http", **_build_http_server(url)}
    else:
        server = _build_stdio_server(collections, default_collection, qdrant_url, config_path, name)
        if not config_path:
            server["env"]["QDRANT_API_KEY"] = ""  # User must fill in

    return json.dumps({"mcpServers": {name: server}}, indent=2)


def generate_cursor(
    collections: list[CollectionConfig],
    default_collection: str,
    qdrant_url: str,
    name: str = "vista-rpms",
    url: str | None = None,
    config_path: str | None = None,
) -> str:
    """Generate Cursor MCP configuration.

    Cursor uses the same ``{ "mcpServers": { ... } }`` format as Claude Desktop.
    """
    return generate_claude_desktop(
        collections, default_collection, qdrant_url, name, url, config_path
    )


def generate_claude_code(
    collections: list[CollectionConfig],
    default_collection: str,
    qdrant_url: str,
    name: str = "vista-rpms",
    url: str | None = None,
    config_path: str | None = None,
) -> str:
    """Generate a ``claude mcp add`` shell command for Claude Code.

    Returns:
        Shell command string.
    """
    if url:
        return f"claude mcp add --transport http {name} {url}"

    parts = [f"claude mcp add {name}"]

    if config_path:
        resolved = str(Path(config_path).resolve())
        parts.append(f"-e QDRANT_API_KEY='<your-api-key>' -- mcp-server-qdrant --config {resolved}")
    else:
        collections_json = json.dumps([c.model_dump(exclude_defaults=False) for c in collections])
        parts.append(f"-e QDRANT_URL='{qdrant_url}'")
        parts.append(f"-e DEFAULT_COLLECTION='{default_collection}'")
        parts.append("-e QDRANT_READ_ONLY='true'")
        parts.append("-e QDRANT_API_KEY='<your-api-key>'")
        parts.append(f"-e COLLECTIONS='{collections_json}'")
        parts.append("-- mcp-server-qdrant")

    return " \\\n  ".join(parts)


# Registry of generator functions by target name
TARGETS = {
    "vscode": generate_vscode,
    "claude-desktop": generate_claude_desktop,
    "cursor": generate_cursor,
    "claude-code": generate_claude_code,
}
