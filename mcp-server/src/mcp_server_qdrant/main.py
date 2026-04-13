import argparse
import json
import sys
from pathlib import Path


def main():
    """
    Main entry point for the mcp-server-qdrant script defined
    in pyproject.toml. It runs the MCP server or generates client configuration.
    """

    parser = argparse.ArgumentParser(description="mcp-server-qdrant")
    subparsers = parser.add_subparsers(dest="command")

    # Default: run the server (also the behaviour when no subcommand is given)
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a JSON configuration file. Settings override environment variables.",
    )

    # generate-config subcommand
    gen = subparsers.add_parser(
        "generate-config",
        help="Generate MCP client configuration for IDEs",
    )
    gen.add_argument(
        "--from-thresher",
        required=True,
        help="Path to a thresher pipeline YAML config file",
    )
    gen.add_argument(
        "--target",
        choices=["vscode", "claude-desktop", "cursor", "claude-code"],
        default="vscode",
        help="Target IDE/client to generate config for (default: vscode)",
    )
    gen.add_argument(
        "--url",
        default=None,
        help="Remote MCP server URL (for HTTP mode). Omit for local stdio mode.",
    )
    gen.add_argument(
        "--name",
        default=None,
        help="Server name in the generated config (default: auto-derived)",
    )
    gen.add_argument(
        "--config-path",
        default=None,
        help="Path to an MCP server JSON config file to reference in stdio mode.",
    )

    args = parser.parse_args()

    if args.command == "generate-config":
        return _run_generate_config(args)

    # Default: run the server
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
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
        "tool_find_description": "TOOL_FIND_DESCRIPTION",
    }

    for json_key, env_key in field_map.items():
        if json_key in config and env_key not in os.environ:
            os.environ[env_key] = str(config[json_key])

    # Complex fields: serialize as JSON strings for pydantic-settings to parse
    if "collections" in config and "COLLECTIONS" not in os.environ:
        os.environ["COLLECTIONS"] = json.dumps(config["collections"])


def _run_generate_config(args) -> None:
    """Generate MCP client configuration from a thresher pipeline YAML."""
    from mcp_server_qdrant.config_gen import TARGETS
    from mcp_server_qdrant.thresher_config import read_thresher_config

    try:
        tc = read_thresher_config(args.from_thresher)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading thresher config: {e}", file=sys.stderr)
        sys.exit(1)

    # Derive server name from target convention if not provided
    name = args.name
    if not name:
        name = "vistaRpms" if args.target == "vscode" else "vista-rpms"

    generator = TARGETS[args.target]
    output = generator(
        collections=tc.collections,
        default_collection=tc.default_collection,
        qdrant_url=tc.qdrant_url,
        name=name,
        url=args.url,
        config_path=args.config_path,
        tool_find_description=tc.tool_find_description,
    )
    print(output)
    sys.exit(0)
