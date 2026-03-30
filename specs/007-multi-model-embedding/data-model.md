# Data Model: Multi-Model Embedding

**Feature**: 007-multi-model-embedding
**Date**: 2026-03-29

## Entities

### EmbeddingModelConfig (new dataclass in `thresher/types.py`)

Configuration for a single named embedding model.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Unique key referencing this model in routing rules |
| `model` | `str` | required | fastembed model identifier (e.g., `nomic-ai/nomic-embed-text-v1.5`) |
| `vector_size` | `int` | required | Embedding vector dimensionality (e.g., 768) |
| `vector_name` | `str` | required | Named vector identifier in Qdrant (e.g., `nomic-v1.5`) |
| `max_tokens` | `int` | `512` | Maximum token count per chunk for this model |
| `index_prefix` | `str` | `""` | Prefix prepended to text at indexing time (e.g., `search_document: `) |
| `query_prefix` | `str` | `""` | Prefix prepended to text at query time (e.g., `search_query: `) |

### EmbeddingConfig (modified dataclass in `thresher/types.py`)

Top-level embedding configuration, backward-compatible.

| Field | Type | Default | Description |
|---|---|---|---|
| `default` | `str` | `"default"` | Name of the default embedding model |
| `models` | `dict[str, EmbeddingModelConfig]` | `{}` | Map of named embedding model configurations |
| `model` | `str` | (legacy) | Legacy single-model field, used when `models` is empty |
| `vector_size` | `int` | (legacy) | Legacy field |
| `vector_name` | `str` | (legacy) | Legacy field |
| `max_tokens` | `int` | (legacy) | Legacy field |

**Backward compatibility**: When `models` is empty and flat fields are present, they are promoted to a single entry named `"default"` in `models`. The `default` field is set to `"default"`.

### RoutingRule (modified dataclass in `thresher/types.py`)

| Field | Type | Default | Change |
|---|---|---|---|
| `collection` | `str` | required | existing |
| `name` | `str` | `""` | existing |
| `file_group` | `list[str]` | `[]` | existing |
| `path` | `list[str]` | `[]` | existing |
| `filename` | `list[str]` | `[]` | existing |
| `embedding` | `str` | `""` | **NEW** — name of the embedding model config; empty means use default |

### RouteResult (new dataclass in `thresher/types.py`)

Return type from `Router.route()`. Replaces the current `str` return.

| Field | Type | Description |
|---|---|---|
| `collection` | `str` | Qdrant collection name |
| `embedding` | `str` | Embedding model config name |

### RoutingConfig (modified in `thresher/types.py`)

| Field | Type | Default | Change |
|---|---|---|---|
| `default_collection` | `str` | `"default"` | existing |
| `default_embedding` | `str` | `""` | **NEW** — default embedding model name; if empty, uses `embedding.default` from top-level config |
| `rules` | `list[RoutingRule]` | `[]` | existing |

## Relationships

```
Config
├── EmbeddingConfig
│   ├── default: str -----------> points to key in models
│   └── models: dict
│       ├── "docs" -> EmbeddingModelConfig(model="nomic-ai/...", vector_name="nomic-v1.5", ...)
│       └── "code" -> EmbeddingModelConfig(model="jinaai/...", vector_name="jina-code-v2", ...)
├── RoutingConfig
│   ├── default_collection: "vista"
│   └── rules:
│       ├── RoutingRule(collection="rpms-source", embedding="code")
│       ├── RoutingRule(collection="rpms", embedding="")  # uses default
│       ├── RoutingRule(collection="vista-source", embedding="code")
│       └── (default: collection="vista", embedding from config default)
└── ...

Router.route(file_path, group) -> RouteResult(collection, embedding)

MultiModelEmbedder
├── _models: dict[str, EmbeddingModelConfig]   # All model configs
├── _active_name: str | None                    # Currently loaded model name
├── _active_model: fastembed.TextEmbedding | None
└── embed_texts(texts, model_name) -> vectors   # Loads/swaps model as needed
```

## Validation Rules

1. `embedding.default` MUST reference a key that exists in `embedding.models`
2. Every `embedding` field in routing rules MUST reference a key in `embedding.models` (or be empty for default)
3. `vector_name` MUST be unique across all models (two models cannot use the same vector name)
4. `vector_size` MUST be a positive integer
5. Legacy config with flat `embedding.model` + no `embedding.models` is auto-promoted to a single `"default"` entry

## State Transitions

**Model Loading (MultiModelEmbedder)**:
```
Initial: _active_name=None, _active_model=None
  |
  v (embed_texts called with model_name="docs")
Loaded: _active_name="docs", _active_model=TextEmbedding("nomic-ai/...")
  |
  v (embed_texts called with model_name="code")
Swapped: _active_name="code", _active_model=TextEmbedding("jinaai/...")
         (previous model dereferenced for GC)
  |
  v (embed_texts called with model_name="code" again)
Cached: _active_name="code" (no reload — same model)
```

## MCP Server Entities

### CollectionConfig (new in `mcp-server/src/mcp_server_qdrant/settings.py`)

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Qdrant collection name |
| `model` | `str` | required | fastembed model identifier |
| `vector_name` | `str` | required | Named vector in Qdrant |
| `vector_size` | `int` | required | Embedding dimensions |
| `index_prefix` | `str` | `""` | Prefix for indexing operations |
| `query_prefix` | `str` | `""` | Prefix for query operations |

### Extended QdrantSettings

| Field | Type | Default | Change |
|---|---|---|---|
| `collections` | `list[CollectionConfig]` | `[]` | **NEW** — per-collection model configs |
| `default_collection` | `str` | `""` | **NEW** — fallback collection name |

When `collections` is non-empty, the server creates one `EmbeddingProvider` per unique model and maps collection names to providers. When `collections` is empty, falls back to the existing single-model behavior.
