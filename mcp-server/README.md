# mcp-server-qdrant: A Qdrant MCP server

> **Note:** This MCP server is based on the [official `mcp-server-qdrant`](https://github.com/qdrant/mcp-server-qdrant)
> but has been modified for use with the [thresher](https://github.com/...) document pipeline. Key differences:
> the write tool (`qdrant-store`) has been removed (thresher indexes documents via its own pipeline);
> search has been extended with `num_results`, `offset` pagination, and `source_path` filtering;
> and multi-collection support with per-collection embedding models has been added.

> The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) is an open protocol that enables
> seamless integration between LLM applications and external data sources and tools. Whether you're building an
> AI-powered IDE, enhancing a chat interface, or creating custom AI workflows, MCP provides a standardized way to
> connect LLMs with the context they need.

This server provides semantic search over Qdrant collections indexed by the thresher document pipeline.

## Overview

A Model Context Protocol server for retrieving content from Qdrant vector collections.
It acts as a semantic search layer on top of Qdrant, intended for use with documents indexed by thresher.

## Components

### Tools

1. `qdrant-find`
   - Retrieve relevant information from the Qdrant database using semantic search
   - Input:
     - `query` (string): Query to use for searching
     - `collection_name` (string): Name of the collection to search in. Required if no default collection
       is configured; not exposed when a default collection is set.
     - `num_results` (integer, optional): Number of results to return. Defaults to `QDRANT_SEARCH_LIMIT`.
       Capped at `QDRANT_SEARCH_LIMIT_MAX` when that is set.
     - `offset` (integer, optional): Number of top-ranked results to skip before returning entries.
       Use with `num_results` for pagination.
     - `source_path` (string, optional): Filter results to entries whose top-level `source` payload field
       exactly matches this value. Useful for narrowing results to a specific source file or GCS path as
       indexed by thresher.
   - Returns: Matching entries as formatted XML strings

## Environment Variables

The configuration of the server is done using environment variables:

| Name                       | Description                                                               | Default Value                                                     |
|----------------------------|---------------------------------------------------------------------------|-------------------------------------------------------------------|
| `QDRANT_URL`               | URL of the Qdrant server                                                  | None                                                              |
| `QDRANT_API_KEY`           | API key for the Qdrant server                                             | None                                                              |
| `COLLECTION_NAME`          | Name of the default collection to use.                                    | None                                                              |
| `QDRANT_LOCAL_PATH`        | Path to the local Qdrant database (alternative to `QDRANT_URL`)           | None                                                              |
| `QDRANT_SEARCH_LIMIT`      | Default number of results returned by `qdrant-find`                       | `10`                                                              |
| `QDRANT_SEARCH_LIMIT_MAX`  | Maximum number of results an LLM may request via `num_results`. Uncapped when unset. | None                                                   |
| `QDRANT_READ_ONLY`         | When `true`, write operations are disabled (store tool is always absent)  | `false`                                                           |
| `EMBEDDING_PROVIDER`       | Embedding provider to use (currently only "fastembed" is supported)       | `fastembed`                                                       |
| `EMBEDDING_MODEL`          | Name of the embedding model to use                                        | `sentence-transformers/all-MiniLM-L6-v2`                          |
| `TOOL_FIND_DESCRIPTION`    | Custom description for the find tool                                      | See default in [`settings.py`](src/mcp_server_qdrant/settings.py) |

Note: You cannot provide both `QDRANT_URL` and `QDRANT_LOCAL_PATH` at the same time.

> [!IMPORTANT]
> Command-line arguments are not supported anymore! Please use environment variables for all configuration.

### FastMCP Environment Variables

Since `mcp-server-qdrant` is based on FastMCP, it also supports all the FastMCP environment variables. The most
important ones are listed below:

| Environment Variable                  | Description                                               | Default Value |
|---------------------------------------|-----------------------------------------------------------|---------------|
| `FASTMCP_DEBUG`                       | Enable debug mode                                         | `false`       |
| `FASTMCP_LOG_LEVEL`                   | Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | `INFO`        |
| `FASTMCP_HOST`                        | Host address to bind the server to                        | `127.0.0.1`   |
| `FASTMCP_PORT`                        | Port to run the server on                                 | `8000`        |
| `FASTMCP_WARN_ON_DUPLICATE_RESOURCES` | Show warnings for duplicate resources                     | `true`        |
| `FASTMCP_WARN_ON_DUPLICATE_TOOLS`     | Show warnings for duplicate tools                         | `true`        |
| `FASTMCP_WARN_ON_DUPLICATE_PROMPTS`   | Show warnings for duplicate prompts                       | `true`        |
| `FASTMCP_DEPENDENCIES`                | List of dependencies to install in the server environment | `[]`          |

## Installation

### Using uvx (recommended)

This fork is **not** published to PyPI (the `mcp-server-qdrant` name there belongs to the upstream project).
Instead, run it directly from this repo with [`uvx`](https://docs.astral.sh/uv/guides/tools/#running-tools)
using the `--from` flag pointed at this repo's `mcp-server` subdirectory:

```shell
QDRANT_URL="http://localhost:6333" \
COLLECTION_NAME="my-collection" \
EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2" \
uvx --from "git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server" mcp-server-qdrant
```

> [!IMPORTANT]
> Always use `--from git+https://github.com/CivicActions/thresher...`. Plain `uvx mcp-server-qdrant`
> will install the unrelated upstream package from PyPI, which does not include the multi-collection
> support, `source_path` filter, or `num_results`/`offset` extensions used by thresher.

You can pin to a specific commit or tag for reproducibility, e.g.
`git+https://github.com/CivicActions/thresher@<sha>#subdirectory=mcp-server`.

#### Transport Protocols

The server supports different transport protocols that can be specified using the `--transport` flag:

```shell
QDRANT_URL="http://localhost:6333" \
COLLECTION_NAME="my-collection" \
uvx --from "git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server" mcp-server-qdrant --transport sse
```

Supported transport protocols:

- `stdio` (default): Standard input/output transport, might only be used by local MCP clients
- `sse`: Server-Sent Events transport, perfect for remote clients
- `streamable-http`: Streamable HTTP transport, perfect for remote clients, more recent than SSE

The default transport is `stdio` if not specified.

When SSE transport is used, the server will listen on the specified port and wait for incoming connections. The default
port is 8000, however it can be changed using the `FASTMCP_PORT` environment variable.

### Using Docker

A Dockerfile is available for building and running the MCP server:

```bash
# Build the container (run from the mcp-server subdirectory of this repo)
docker build -t thresher-mcp-server-qdrant .

# Run the container
docker run -p 8000:8000 \
  -e FASTMCP_HOST="0.0.0.0" \
  -e QDRANT_URL="http://your-qdrant-server:6333" \
  -e QDRANT_API_KEY="your-api-key" \
  -e COLLECTION_NAME="your-collection" \
  thresher-mcp-server-qdrant
```

> [!TIP]
> Please note that we set `FASTMCP_HOST="0.0.0.0"` to make the server listen on all network interfaces. This is
> necessary when running the server in a Docker container.

### MCP client configuration

For end-user MCP client configuration (Claude Code, Claude Desktop, VS Code, Cursor) including the
ready-to-use settings for the public VistA & RPMS archive, see the
[vista-rpms-archive README](https://github.com/CivicActions/vista-rpms-archive#mcp-server-configuration).

The general pattern for any MCP client is:

```json
{
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/CivicActions/thresher@main#subdirectory=mcp-server",
    "mcp-server-qdrant"
  ],
  "env": {
    "QDRANT_URL": "https://your-qdrant-server",
    "QDRANT_API_KEY": "your_api_key",
    "COLLECTION_NAME": "your-collection-name",
    "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2"
  }
}
```

This MCP server will use the collection(s) specified. Documents must be indexed into Qdrant by the thresher
pipeline before searches will return results.

## Run MCP server in Development Mode

The MCP server can be run in development mode using the `fastmcp dev` command. This will start the server and open
the MCP inspector in your browser.

```shell
QDRANT_URL=":memory:" COLLECTION_NAME="test" \
fastmcp dev src/mcp_server_qdrant/server.py
```

Once started, open your browser to http://localhost:5173 to access the inspector interface.

## Contributing

If you have suggestions for improvements or want to report a bug, open an issue!

## License

This MCP server is licensed under the Apache License 2.0. This means you are free to use, modify, and distribute the
software, subject to the terms and conditions of the Apache License 2.0. For more details, please see the LICENSE file
in the project repository.
