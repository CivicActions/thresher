import argparse
import json
from pathlib import Path


def main():
    """
    Main entry point for the mcp-server-qdrant script defined
    in pyproject.toml. It runs the MCP server with a specific transport
    protocol.
    """

    # Parse the command-line arguments to determine the transport protocol.
    parser = argparse.ArgumentParser(description="mcp-server-qdrant")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a JSON configuration file. Settings in the file override environment variables.",
    )
    args = parser.parse_args()

    # Load JSON config file if provided and inject into environment before importing settings
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            import os
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            _apply_json_config(config_data)

    # Import is done here to make sure environment variables are loaded
    # only after we make the changes.
    from mcp_server_qdrant.server import mcp

    mcp.run(transport=args.transport)


def _apply_json_config(config: dict) -> None:
    """Apply JSON config values as environment variables for pydantic-settings to pick up.

    Only sets variables that are not already present in the environment so that
    explicit env vars take precedence over the config file.
    """
    import os

    field_map = {
        "qdrant_url": "QDRANT_URL",
        "qdrant_api_key": "QDRANT_API_KEY",
        "collection_name": "COLLECTION_NAME",
        "default_collection": "DEFAULT_COLLECTION",
        "read_only": "QDRANT_READ_ONLY",
        "search_limit": "QDRANT_SEARCH_LIMIT",
    }

    for json_key, env_key in field_map.items():
        if json_key in config and env_key not in os.environ:
            os.environ[env_key] = str(config[json_key])
