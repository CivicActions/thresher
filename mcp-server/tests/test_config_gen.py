"""Tests for config_gen.py — MCP client configuration generation."""

import json

from mcp_server_qdrant.config_gen import (
    generate_claude_code,
    generate_claude_desktop,
    generate_cursor,
    generate_vscode,
)
from mcp_server_qdrant.settings import CollectionConfig

# Shared fixture data
COLLECTIONS = [
    CollectionConfig(
        name="rpms-source",
        model="jinaai/jina-embeddings-v2-base-code",
        vector_name="jina-code-v2",
        vector_size=768,
    ),
    CollectionConfig(
        name="vista",
        model="nomic-ai/nomic-embed-text-v1.5",
        vector_name="nomic-v1.5",
        vector_size=768,
        query_prefix="search_query: ",
    ),
]


class TestGenerateVSCode:
    """Tests for VS Code mcp.json generation."""

    def test_stdio_mode_with_env(self):
        """Stdio mode without config file passes settings via env vars."""
        output = generate_vscode(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vistaRpms",
        )
        data = json.loads(output)

        assert "servers" in data
        server = data["servers"]["vistaRpms"]
        assert server["type"] == "stdio"
        assert "command" in server
        assert "env" in server
        assert server["env"]["QDRANT_URL"] == "https://qdrant.example.com:6333"
        assert server["env"]["DEFAULT_COLLECTION"] == "vista"
        assert server["env"]["QDRANT_READ_ONLY"] == "true"
        assert "COLLECTIONS" in server["env"]
        # Verify COLLECTIONS is valid JSON
        collections_data = json.loads(server["env"]["COLLECTIONS"])
        assert len(collections_data) == 2

        # Should have inputs for Qdrant API key
        assert "inputs" in data
        assert any(i["id"] == "qdrantApiKey" for i in data["inputs"])

    def test_stdio_mode_with_config_file(self):
        """Stdio mode with config file references the file path."""
        output = generate_vscode(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vistaRpms",
            config_path="/path/to/mcp-config.json",
        )
        data = json.loads(output)

        server = data["servers"]["vistaRpms"]
        assert server["type"] == "stdio"
        assert "--config" in server.get("args", [])

    def test_http_mode(self):
        """HTTP mode generates url + headers."""
        output = generate_vscode(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vistaRpms",
            url="https://mcp.example.com/mcp",
        )
        data = json.loads(output)

        server = data["servers"]["vistaRpms"]
        assert server["type"] == "http"
        assert server["url"] == "https://mcp.example.com/mcp"
        assert "Authorization" in server["headers"]

        # Should have inputs for MCP API key
        assert any(i["id"] == "mcpApiKey" for i in data["inputs"])


class TestGenerateClaudeDesktop:
    """Tests for Claude Desktop configuration generation."""

    def test_stdio_mode(self):
        """Produces mcpServers format for Claude Desktop."""
        output = generate_claude_desktop(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vista-rpms",
        )
        data = json.loads(output)

        assert "mcpServers" in data
        server = data["mcpServers"]["vista-rpms"]
        assert "command" in server

    def test_http_mode(self):
        """HTTP mode produces url + headers."""
        output = generate_claude_desktop(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vista-rpms",
            url="https://mcp.example.com/mcp",
        )
        data = json.loads(output)

        server = data["mcpServers"]["vista-rpms"]
        assert server["type"] == "http"
        assert server["url"] == "https://mcp.example.com/mcp"


class TestGenerateCursor:
    """Tests for Cursor configuration generation."""

    def test_uses_same_format_as_claude_desktop(self):
        """Cursor format matches Claude Desktop."""
        cursor_output = generate_cursor(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vista-rpms",
        )
        desktop_output = generate_claude_desktop(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vista-rpms",
        )
        assert cursor_output == desktop_output


class TestGenerateClaudeCode:
    """Tests for Claude Code command generation."""

    def test_stdio_mode(self):
        """Produces a claude mcp add command."""
        output = generate_claude_code(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vista-rpms",
        )
        assert output.startswith("claude mcp add vista-rpms")
        assert "QDRANT_URL" in output
        assert "COLLECTIONS" in output

    def test_http_mode(self):
        """HTTP mode produces a transport http command."""
        output = generate_claude_code(
            collections=COLLECTIONS,
            default_collection="vista",
            qdrant_url="https://qdrant.example.com:6333",
            name="vista-rpms",
            url="https://mcp.example.com/mcp",
        )
        assert "--transport http" in output
        assert "https://mcp.example.com/mcp" in output
