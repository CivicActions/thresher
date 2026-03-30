# Implementation Plan: Multi-Model Embedding with Custom MCP Server

**Branch**: `007-multi-model-embedding` | **Date**: 2026-03-29 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/007-multi-model-embedding/spec.md`

## Summary

Extend the thresher pipeline to support multiple embedding models selected per routing rule, so document collections use `nomic-ai/nomic-embed-text-v1.5` (768d, text-optimized) and source code collections use `jinaai/jina-embeddings-v2-base-code` (768d, code-aware). Adapt the MCP server in `mcp-server/` to select the correct embedding model per collection at query time. Add a `thresher mcp-config` CLI subcommand to generate MCP server configuration from the pipeline config.

## Technical Context

**Language/Version**: Python 3.13 (Docker), 3.14.3 (local dev)
**Primary Dependencies**: fastembed (ONNX embeddings), qdrant-client (vector store), fastmcp (MCP server), pydantic (settings)
**Storage**: Qdrant (vector search), GCS (document/queue storage)
**Testing**: pytest (unit + functional), ruff (lint), ty (type check)
**Target Platform**: Linux containers on GKE, macOS dev
**Project Type**: CLI pipeline + MCP server
**Performance Goals**: Index 700K files with 64-128 parallel runners; MCP query response <5s excluding model load
**Constraints**: Runner pods have 8Gi memory request/32Gi limit; only one embedding model loaded at a time per runner (lazy loading)
**Scale/Scope**: ~700K files, ~5.8M batches, 4 Qdrant collections, 2 embedding models

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Configuration-Driven Design | PASS | All model selections, vector names, prefixes, and collection-model mappings are in YAML config. No hard-coded model names in processing code. Backward-compatible with existing single-model config. |
| II. Extensible Architecture | PASS | `Embedder` gains a registry of named models. Adding a third model requires only a config entry. MCP server's `EmbeddingProvider` abstraction already supports multiple implementations. |
| III. Reliability First | PASS | Startup validation ensures all referenced embedding names exist. Lazy loading prevents OOM. Existing test patterns extended for multi-model scenarios. |
| IV. Performance at Scale | PASS | Lazy loading means only one model in RAM at a time per runner. If a runner processes a batch with mixed collections, models swap (GC reclaims previous). No concurrent multi-model loading. |
| V. Cloud-Native Design | PASS | Configuration via env vars preserved. Dockerfile pre-downloads both models. MCP server configurable via env vars or generated config. |

**Gate result**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/007-multi-model-embedding/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
thresher/
├── config.py            # MODIFY: parse embedding.models map, backward compat
├── config_schema.json   # MODIFY: add embedding.models schema, routing rule embedding field
├── defaults.yaml        # MODIFY: add embedding.models with single default model
├── types.py             # MODIFY: add EmbeddingModelConfig, extend RoutingRule with embedding field
├── embedder.py          # MODIFY: MultiModelEmbedder with lazy model registry
├── cli.py               # MODIFY: add mcp-config subcommand
├── runner/
│   ├── loop.py          # MODIFY: pass config instead of single embedder to processor
│   └── processor.py     # MODIFY: select embedder by routing rule's embedding assignment
└── processing/
    └── router.py        # MODIFY: Router.route() returns (collection, embedding_name) tuple

mcp-server/              # MODIFY: existing standalone MCP server
├── pyproject.toml       # UPDATE: version bump
├── src/mcp_server_qdrant/
│   ├── settings.py      # MODIFY: add multi-collection config with per-collection models
│   ├── qdrant.py        # MODIFY: QdrantConnector selects embedding provider per collection
│   ├── server.py        # MODIFY: tools accept collection_name, route to correct embedder
│   └── embeddings/
│       ├── base.py      # MODIFY: add embed_query_prefix / embed_document_prefix support
│       ├── factory.py   # MODIFY: create multiple providers from collection config
│       └── fastembed.py # MODIFY: support prefix prepending
└── tests/               # ADD: tests for multi-collection routing

tests/
├── unit/
│   ├── test_embedder.py      # ADD: MultiModelEmbedder tests
│   ├── test_config.py        # MODIFY: add multi-model config parsing tests
│   └── test_router.py        # MODIFY: add embedding field in routing tests
└── functional/
    └── test_pipeline_e2e.py  # MODIFY: test with multi-model config

.github/workflows/
└── ci.yml               # MODIFY: add MCP server lint/test job
```

**Structure Decision**: Two codebases in one repo — thresher pipeline at root, MCP server in `mcp-server/` subdirectory. Each has its own pyproject.toml and venv. CI runs both. The `thresher mcp-config` command bridges configuration from pipeline to MCP server.