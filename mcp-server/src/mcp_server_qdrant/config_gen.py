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

# This fork is not published to PyPI (the `mcp-server-qdrant` name on PyPI belongs
# to the unrelated upstream project). When falling back to uvx, point it at this
# repo's `mcp-server` subdirectory so it builds and runs the correct package.
UVX_SOURCE = "git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server"


def _find_server_command() -> tuple[str, list[str]]:
    """Determine the command + leading args needed to launch the MCP server.

    Returns ``("mcp-server-qdrant", [])`` if the script is on PATH, otherwise
    ``("uvx", ["--from", UVX_SOURCE, "mcp-server-qdrant"])`` so end users do not
    need a local install.
    """
    if shutil.which("mcp-server-qdrant"):
        return "mcp-server-qdrant", []
    return "uvx", ["--from", UVX_SOURCE, "mcp-server-qdrant"]


def _build_stdio_server(
    collections: list[CollectionConfig],
    default_collection: str,
    qdrant_url: str,
    config_path: str | None,
    name: str,
    tool_find_description: str | None = None,
) -> dict:
    """Build a stdio server config dict (command/args/env).

    If config_path is provided, the server is launched with ``--config <path>``.
    Otherwise, configuration is passed via environment variables.
    """
    cmd, args = _find_server_command()
    args = list(args)
    if config_path:
        args.extend(["--config", str(Path(config_path).resolve())])

    env: dict[str, str] = {}

    if not config_path:
        # Pass config via env vars when no config file
        env["QDRANT_URL"] = qdrant_url
        env["DEFAULT_COLLECTION"] = default_collection
        env["QDRANT_READ_ONLY"] = "true"
        env["COLLECTIONS"] = json.dumps([c.model_dump(exclude_defaults=False) for c in collections])
        if tool_find_description:
            env["TOOL_FIND_DESCRIPTION"] = tool_find_description

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
    tool_find_description: str | None = None,
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
            **_build_stdio_server(
                collections,
                default_collection,
                qdrant_url,
                config_path,
                name,
                tool_find_description,
            ),
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
    tool_find_description: str | None = None,
) -> str:
    """Generate Claude Desktop configuration.

    Claude Desktop uses ``{ "mcpServers": { ... } }`` format.
    """
    if url:
        server = {"type": "http", **_build_http_server(url)}
    else:
        server = _build_stdio_server(
            collections,
            default_collection,
            qdrant_url,
            config_path,
            name,
            tool_find_description,
        )
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
    tool_find_description: str | None = None,
) -> str:
    """Generate Cursor MCP configuration.

    Cursor uses the same ``{ "mcpServers": { ... } }`` format as Claude Desktop.
    """
    return generate_claude_desktop(
        collections,
        default_collection,
        qdrant_url,
        name,
        url,
        config_path,
        tool_find_description,
    )


def generate_claude_code(
    collections: list[CollectionConfig],
    default_collection: str,
    qdrant_url: str,
    name: str = "vista-rpms",
    url: str | None = None,
    config_path: str | None = None,
    tool_find_description: str | None = None,
) -> str:
    """Generate a ``claude mcp add`` shell command for Claude Code.

    Returns:
        Shell command string.
    """
    if url:
        return f"claude mcp add --transport http {name} {url}"

    cmd, prefix_args = _find_server_command()
    launch = " ".join([cmd, *prefix_args])

    parts = [f"claude mcp add {name}"]

    if config_path:
        resolved = str(Path(config_path).resolve())
        parts.append(f"-e QDRANT_API_KEY='<your-api-key>' -- {launch} --config {resolved}")
    else:
        collections_json = json.dumps([c.model_dump(exclude_defaults=False) for c in collections])
        parts.append(f"-e QDRANT_URL='{qdrant_url}'")
        parts.append(f"-e DEFAULT_COLLECTION='{default_collection}'")
        parts.append("-e QDRANT_READ_ONLY='true'")
        parts.append("-e QDRANT_API_KEY='<your-api-key>'")
        parts.append(f"-e COLLECTIONS='{collections_json}'")
        if tool_find_description:
            parts.append(f"-e TOOL_FIND_DESCRIPTION='{tool_find_description}'")
        parts.append(f"-- {launch}")

    return " \\\n  ".join(parts)


# Registry of generator functions by target name
TARGETS = {
    "vscode": generate_vscode,
    "claude-desktop": generate_claude_desktop,
    "cursor": generate_cursor,
    "claude-code": generate_claude_code,
}
