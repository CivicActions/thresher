# Research: Multi-Model Embedding with Custom MCP Server

**Feature**: 007-multi-model-embedding
**Date**: 2026-03-29

## R1: Config Schema Design for Multi-Model Embedding

**Question**: How should the configuration schema support multiple named embedding models while maintaining backward compatibility with the existing single-model `embedding` block?

**Decision**: Use an `embedding.models` map alongside the existing flat fields. When `embedding.models` is present, it takes precedence. When absent, the flat fields (`model`, `vector_size`, `vector_name`, `max_tokens`) are promoted to a single default model entry named `"default"`.

**Rationale**: This preserves 100% backward compatibility — existing configs work unchanged. The `models` map with named entries allows routing rules to reference models by name. Adding `embedding.default` specifies which named model is used when a routing rule has no explicit `embedding` field.

**Alternatives considered**:
- *Per-collection embedding config*: Would require knowing all collections upfront in config, but collections are dynamically derived from routing rules. Rejected.
- *Separate embedding config file*: Adds deployment complexity. Rejected.
- *Embedding field on file_type_groups*: Conflates file classification with embedding strategy. Rejected — embedding choice depends on the target collection, not the file type.

**Config example**:
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

## R2: Router Extension for Embedding Selection

**Question**: How should routing rules specify which embedding model to use?

**Decision**: Add an optional `embedding` field to routing rules. The router's `route()` method returns a `RouteResult` (collection name + embedding model name). When `embedding` is not set on a rule, the default from `embedding.default` is used.

**Rationale**: The router already handles the collection assignment logic. Embedding selection is a natural extension — the same rule that routes a MUMPS file to `vista-source` also specifies the `code` embedding model. This keeps configuration co-located and avoids a separate mapping layer.

**Alternatives considered**:
- *Separate collection-to-model mapping table*: Requires maintaining two config locations for the same logical decision. Rejected.
- *Automatic inference from file_type_group*: Not all code files should use the code model (e.g., if routed to a docs collection by path). Rejected — the decision depends on collection, not file type.

## R3: Embedder Architecture — Single Class vs Registry

**Question**: Should the embedder be a single class with multiple model instances, or a registry/factory pattern?

**Decision**: Extend the existing `Embedder` class into `MultiModelEmbedder` that manages a dict of named `Embedder` instances (one per model config). Only one model is loaded at a time (lazy loading). When a different model is requested, the previous one is discarded (set to `None` for GC).

**Rationale**: Runner pods process files sequentially. Within a batch of 250 files, most files likely route to the same collection and model. Lazy loading avoids the ~550MB RAM cost of both models simultaneously. Model swapping is rare within a batch and the ~2-5s reload cost is acceptable.

**Alternatives considered**:
- *Keep both models loaded*: Would consume ~1.1GB for embeddings alone, leaving less room for docling. Acceptable but wasteful since batches are usually homogeneous. Rejected.
- *Separate embedder per runner pod*: Would require model-aware batch assignment. Adds orchestration complexity. Rejected.

## R4: MCP Server Multi-Collection Architecture

**Question**: How should the MCP server handle multiple collections with different embedding models?

**Decision**: Replace the single `EmbeddingProvider` in `QdrantConnector` with a dict of providers keyed by collection name. The `search()` and `store()` methods look up the provider for the requested collection. Settings are extended with a `collections` list, each defining a collection name, model, vector name, and optional prefixes.

**Rationale**: The MCP server already supports passing collection_name to tools. The only missing piece is selecting the correct embedding model per collection. A provider-per-collection pattern is straightforward and matches the pipeline's routing logic.

**Alternatives considered**:
- *Single model, re-embed for each collection*: Would use wrong embeddings for code collections. Rejected — defeats the purpose.
- *Multiple MCP server instances*: Each serving one collection with one model. Adds deployment complexity. Rejected.

## R5: Prefix Handling for Nomic Model

**Question**: How should task-specific prefixes be handled for nomic-embed-text-v1.5?

**Decision**: Add `index_prefix` and `query_prefix` fields to the embedding model config. The pipeline embedder prepends `index_prefix` before embedding chunks. The MCP server embedding provider prepends `query_prefix` before embedding search queries. Both fields default to empty string (no prefix).

**Rationale**: Nomic v1.5 requires `search_document: ` for document embedding and `search_query: ` for query embedding. This is model-specific behavior that belongs in configuration, not hard-coded. Other models like Jina v2-code have no prefix requirement and would leave these fields empty.

**Alternatives considered**:
- *Hard-code prefix logic per known model name*: Violates Constitution Principle I (Configuration-Driven Design). Rejected.
- *Use fastembed's built-in passage_embed/query_embed*: fastembed's `TextEmbedding.embed()` does not auto-prefix. The `passage_embed()` / `query_embed()` methods exist in the MCP server's `FastEmbedProvider` but not in the pipeline's `Embedder`. Adding prefix at config level is simpler and consistent across both codebases.

## R6: fastembed Compatibility for Selected Models

**Question**: Are the selected models available in fastembed?

**Decision**: Both models are confirmed available in fastembed:
- `nomic-ai/nomic-embed-text-v1.5` — available via fastembed's ONNX model registry
- `jinaai/jina-embeddings-v2-base-code` — available via fastembed

**Rationale**: fastembed is the embedding backend for both the pipeline (`thresher/embedder.py`) and the MCP server (`mcp-server/src/mcp_server_qdrant/embeddings/fastembed.py`). Using models from fastembed's registry ensures ONNX-optimized inference without additional conversion steps.

## R7: MCP Server Configuration Generation

**Question**: What format should the `thresher mcp-config` output use?

**Decision**: Output a JSON object that can be used directly as MCP server configuration (environment variables or config file). The output includes: Qdrant URL, API key, collection definitions with their models and vector names, and read-only flag.

**Rationale**: JSON is machine-readable and can be consumed by MCP client configurations (e.g., Claude Desktop's `claude_desktop_config.json`). It matches the env-var-based configuration pattern of the MCP server.

**Alternatives considered**:
- *YAML output*: MCP clients expect JSON. Rejected.
- *.env file format*: Cannot represent nested collection-to-model mappings. Rejected.
- *Direct env var export*: `export FOO=bar` format is shell-specific and cannot represent the collections list. Rejected.
