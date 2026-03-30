# Contract: Multi-Model Embedder

**Feature**: 007-multi-model-embedding

## Interface: MultiModelEmbedder

Replaces the current single-model `Embedder` class. Manages a registry of named model configurations and lazy-loads one model at a time.

### Constructor

```python
MultiModelEmbedder(models: dict[str, EmbeddingModelConfig])
```

- `models`: Map of named embedding model configurations (from `config.embedding.models`)
- Raises `ValueError` if `models` is empty

### Methods

#### `embed_texts(texts: Sequence[str], model_name: str) -> list[list[float]]`

Embed a list of texts using the specified named model. Prepends `index_prefix` if configured.

- **Precondition**: `model_name` exists in the models registry
- **Behavior**: If the requested model is not currently loaded, unloads the previous model and loads the requested one
- **Returns**: List of embedding vectors, each of length `models[model_name].vector_size`
- **Raises**: `KeyError` if `model_name` not in registry; `RuntimeError` if model fails to load

#### `preload(model_name: str) -> None`

Pre-load a specific model (call during worker initialization).

- **Behavior**: Loads the model into memory, replacing any previously loaded model

#### `get_model_config(model_name: str) -> EmbeddingModelConfig`

Return the configuration for a named model.

- **Raises**: `KeyError` if `model_name` not in registry

### Backward Compatibility

The `Embedder` class remains available for tests and simple use cases. `MultiModelEmbedder` is the production replacement used by `FileProcessor`.

---

## Interface: Router.route() (updated return type)

### Current

```python
def route(self, file_path: str, file_type_group: str | None = None) -> str
```

Returns: collection name

### Updated

```python
def route(self, file_path: str, file_type_group: str | None = None) -> RouteResult
```

Returns: `RouteResult(collection=str, embedding=str)` where `embedding` is the model config name (or the default if the matching rule has no explicit `embedding` field).

### Migration

All callers of `router.route()` must destructure the result:
- `collection = router.route(...)` → `result = router.route(...); collection = result.collection`

---

## Interface: MCP Server Embedding Provider per Collection

### QdrantConnector (updated)

```python
class QdrantConnector:
    def __init__(
        self,
        embedding_providers: dict[str, EmbeddingProvider],
        default_collection: str,
        ...
    )
```

When `collections` config is present:
- `embedding_providers` maps collection name → EmbeddingProvider instance
- `search()` and `store()` look up the provider by collection name

When `collections` config is absent (backward compat):
- Single provider passed as `{"_default": provider}`
- All operations use `_default` provider regardless of collection name

### Tool Signatures

#### `qdrant-find`

```
find(query: str, collection_name: str) -> list[str]
```

- Collection name is required when multiple collections are configured
- Collection name is omitted from signature when a single default collection is set

#### `qdrant-store`

```
store(information: str, collection_name: str, metadata: dict | None = None) -> str
```

- Same collection_name behavior as `find`
- Disabled when `read_only=True`

---

## Interface: `thresher mcp-config` CLI Subcommand

### Usage

```bash
thresher --config prod-config.yaml mcp-config
```

### Output (JSON to stdout)

```json
{
  "qdrant_url": "https://qdrant.example.com:443",
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

The collections list is derived by walking routing rules and the default collection to enumerate all possible collections with their assigned embedding model config.
