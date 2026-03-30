# PR: Multi-model embedding + MCP server improvements

## Summary

This PR adds multi-model embedding support to the thresher pipeline and extends the MCP server with search improvements. Different file types can now be embedded with different models (e.g. nomic for documents, jina for code), and the MCP server has been tightened up for search-only use.

## Changes

### Multi-model embedding (spec 007)

The pipeline now supports routing different file types to different embedding models. Each model loads lazily and only one is resident in memory at a time, so memory usage does not grow with the number of configured models.

**Pipeline core** (`phases 1–3`)

- `types.py`: Added `EmbeddingModelConfig`, `RouteResult`, `IndexChunk.vector_name`, and `RoutingRule.embedding` field
- `config_schema.json`: Added `embedding.models` map and `embedding.default`; routing rules accept an `embedding` field
- `config.py`: `_parse_embedding_config()` promotes legacy flat embedding config to a `"default"` model entry (full backward compatibility)
- `embedder.py`: New `MultiModelEmbedder` — lazy loading, one active model at a time, `index_prefix` per model
- `processing/router.py`: `Router.route()` now returns `RouteResult(collection, embedding)` instead of a bare collection string
- `runner/processor.py`, `runner/loop.py`, `cli.py`: Updated to use `MultiModelEmbedder` and `RouteResult`
- `providers/qdrant.py`: `index_chunks` uses `chunk.vector_name` to route to the correct named vector

**MCP server** (`phases 4–5`)

- `settings.py`: New `CollectionConfig` model; `QdrantSettings` gains `collections` and `default_collection`
- `embeddings/fastembed.py`: `index_prefix` / `query_prefix` support
- `embeddings/factory.py`: `create_collection_providers()` — creates per-collection providers, reusing instances when model+prefixes match
- `qdrant.py`: `_get_provider()` for per-collection routing; `embedding_providers` dict on `QdrantConnector`
- `mcp_server.py`: Wires per-collection providers when `collections` config present
- `main.py`: `--config JSON` argument for passing collection config as a file

**CLI + polish** (`phases 6–8`)

- `thresher mcp-config` subcommand: outputs JSON describing all configured collections (model, vector name, prefixes) ready to paste into MCP server config
- `.github/workflows/ci.yml`: New `mcp-server` CI job (ruff, mypy, pytest) triggered on changes to `mcp-server/`
- `Dockerfile`: Pre-downloads all three embedding models at build time
- `config.example.yaml`: Multi-model embedding examples

### MCP server search improvements

- **Remove `qdrant-store`** — write tool removed entirely. Thresher indexes documents; the MCP server is read-only.
- **`num_results`** — optional integer param on `qdrant-find`; lets the LLM request a specific result count
- **`QDRANT_SEARCH_LIMIT_MAX`** — new env var that hard-caps any LLM-requested `num_results` (default: uncapped)
- **`offset`** — optional integer param for pagination; passes directly to `query_points` (confirmed supported by qdrant-client)
- **`source_path`** — optional string param that filters on the top-level `"source"` payload field written by thresher, allowing results to be scoped to a specific source file
- **README** updated to reflect all of the above; adds a note that this is a fork of the official `mcp-server-qdrant`

## Tests

| Suite | Before | After |
|-------|--------|-------|
| Pipeline unit tests | 504 | 563 |
| MCP server tests | 0 | 52 |
| **Total** | **504** | **615** |

New test files:
- `tests/unit/test_embedder.py` — `MultiModelEmbedder` (lazy loading, model swap, prefix)
- `tests/unit/test_config.py` — multi-model config parsing and validation
- `tests/unit/test_cli.py` — `mcp-config` subcommand output
- `mcp-server/tests/test_settings.py` — `CollectionConfig`, `QdrantSettings`, `search_limit_max`
- `mcp-server/tests/test_multi_collection.py` — per-collection provider routing
- `mcp-server/tests/test_prefix_handling.py` — index/query prefix prepending
- `mcp-server/tests/test_qdrant_integration.py` — limit, offset, source_path filter

Updated tests: `test_router.py`, `test_polish.py`, `test_runner.py`, `test_skip_list.py` (updated for `RouteResult` return type).

## Configuration example

```yaml
embedding:
  default: nomic
  models:
    nomic:
      model: nomic-ai/nomic-embed-text-v1.5
      vector_size: 768
      vector_name: nomic-v1.5
      index_prefix: "search_document: "
      query_prefix: "search_query: "
    jina-code:
      model: jinaai/jina-embeddings-v2-base-code
      vector_size: 768
      vector_name: jina-code-v2

routing_rules:
  - name: code
    extensions: [.py, .ts, .go, .rs, .java]
    collection: thresher-code
    embedding: jina-code
  - name: documents
    extensions: [.pdf, .docx, .md]
    collection: thresher-docs
    embedding: nomic
```

Generate MCP server config for the above:

```shell
thresher mcp-config --config config.yaml
```

## Notes

- **Backward compatible**: all existing single-model configs continue to work unchanged
- `IndexChunk.vector_name` defaults to `""` with a fallback in `QdrantDestinationProvider` to the legacy `self.vector_name`
- The `qdrant-store` tool is gone but `QdrantConnector.store()` is kept for integration tests
