# Quickstart: Multi-Model Embedding

**Feature**: 007-multi-model-embedding

## 1. Configure Multi-Model Embedding

Add the `embedding.models` map and `embedding.default` to your config YAML:

```yaml
embedding:
  default: docs
  models:
    docs:
      model: "nomic-ai/nomic-embed-text-v1.5"
      vector_size: 768
      vector_name: "nomic-v1.5"
      max_tokens: 512
      index_prefix: "search_document: "
      query_prefix: "search_query: "
    code:
      model: "jinaai/jina-embeddings-v2-base-code"
      vector_size: 768
      vector_name: "jina-code-v2"
      max_tokens: 512
```

## 2. Assign Models to Routing Rules

Add the `embedding` field to routing rules that should use a non-default model:

```yaml
routing:
  default_collection: "vista"
  rules:
    - name: rpms-source-code
      file_group: ["mumps-source", "mumps-globals", "general-source"]
      path: ["^(?i).*ihs\\.gov", "^(?i).*rpms"]
      collection: rpms-source
      embedding: code          # Uses jina code model
    - name: rpms-docs
      path: ["^(?i).*ihs\\.gov", "^(?i).*rpms"]
      collection: rpms
      # No embedding field → uses default ("docs")
    - name: vista-source-code
      file_group: ["mumps-source", "mumps-globals", "general-source"]
      collection: vista-source
      embedding: code
```

## 3. Run the Pipeline

No CLI changes. The controller and runner commands work as before:

```bash
# Controller scans and queues files (unchanged)
thresher --config prod-config.yaml controller

# Runner processes batches, selecting the correct model per routing rule
thresher --config prod-config.yaml runner
```

The runner's `FileProcessor` receives a `RouteResult(collection, embedding)` from the router and passes the embedding name to `MultiModelEmbedder.embed_texts()`. Models lazy-load on first use and swap when the embedding name changes.

## 4. Generate MCP Server Configuration

After indexing, generate the MCP server config from the pipeline config:

```bash
thresher --config prod-config.yaml mcp-config
```

Output (JSON to stdout):

```json
{
  "qdrant_url": "https://qdrant.cicd.civicactions.net:443",
  "qdrant_api_key": "...",
  "default_collection": "vista",
  "read_only": true,
  "collections": [
    {
      "name": "vista",
      "model": "nomic-ai/nomic-embed-text-v1.5",
      "vector_name": "nomic-v1.5",
      "vector_size": 768,
      "query_prefix": "search_query: "
    },
    {
      "name": "rpms",
      "model": "nomic-ai/nomic-embed-text-v1.5",
      "vector_name": "nomic-v1.5",
      "vector_size": 768,
      "query_prefix": "search_query: "
    },
    {
      "name": "vista-source",
      "model": "jinaai/jina-embeddings-v2-base-code",
      "vector_name": "jina-code-v2",
      "vector_size": 768,
      "query_prefix": ""
    },
    {
      "name": "rpms-source",
      "model": "jinaai/jina-embeddings-v2-base-code",
      "vector_name": "jina-code-v2",
      "vector_size": 768,
      "query_prefix": ""
    }
  ]
}
```

Pipe to a file for the MCP server:

```bash
thresher --config prod-config.yaml mcp-config > mcp-server/config.json
```

## 5. Start the MCP Server

```bash
cd mcp-server
uv run mcp-server-qdrant --config config.json
```

Or configure via environment variables (backward-compatible single-collection mode):

```bash
QDRANT_URL="https://qdrant.cicd.civicactions.net:443" \
QDRANT_API_KEY="..." \
COLLECTION_NAME="vista" \
EMBEDDING_MODEL="nomic-ai/nomic-embed-text-v1.5" \
uv run mcp-server-qdrant
```

## 6. Backward Compatibility

Existing configs without `embedding.models` continue to work unchanged. The flat fields are treated as a single `"default"` model:

```yaml
# This still works — equivalent to a single-model "default" entry
embedding:
  model: "sentence-transformers/all-MiniLM-L6-v2"
  vector_size: 384
  vector_name: "fast-all-minilm-l6-v2"
  max_tokens: 512
```

## 7. Verify

```bash
# Run unit tests
uv run pytest tests/unit/test_embedder.py tests/unit/test_config.py tests/unit/test_router.py -v

# Run functional tests (requires Docker services)
docker compose -f docker-compose.functional.yaml up -d
uv run pytest tests/functional/ -v

# Lint + format + type check
uv run prek
```
