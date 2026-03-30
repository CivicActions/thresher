# Tasks: Multi-Model Embedding with Custom MCP Server

**Input**: Design documents from `/specs/007-multi-model-embedding/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add new types and update config schema to support multi-model embedding

- [X] T001 Add `EmbeddingModelConfig` and `RouteResult` dataclasses to thresher/types.py and extend `RoutingRule` with optional `embedding` field
- [X] T002 [P] Update thresher/config_schema.json to add `embedding.models` map, `embedding.default` field, and `embedding` field on routing rule items

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config parsing and validation that MUST be complete before any user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Update `EmbeddingConfig` dataclass and `_merge_configs()` in thresher/config.py to parse `embedding.models` map with backward compatibility (flat fields promoted to single `"default"` entry when `models` absent)
- [X] T004 Add startup validation in thresher/config.py `validate_config()` to check all `embedding` names in routing rules reference keys in `embedding.models` and `embedding.default` references a valid model
- [X] T005 [P] Add unit tests for multi-model config parsing and backward-compatible single-model promotion in tests/unit/test_config.py

**Checkpoint**: Configuration infrastructure ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Per-Collection Embedding Models (Priority: P1) 🎯 MVP

**Goal**: Pipeline selects the correct embedding model per routing rule, so document collections use the text model and source code collections use the code model.

**Independent Test**: Configure two embedding models in YAML, process one document file and one source code file, verify each is embedded with the correct model and indexed into the correct collection with the correct vector name.

### Implementation for User Story 1

- [X] T006 [P] [US1] Create `MultiModelEmbedder` class with lazy model loading, model swapping, and `index_prefix` prepending in thresher/embedder.py (keep existing `Embedder` class for backward compat)
- [X] T007 [P] [US1] Update `Router.route()` to return `RouteResult(collection, embedding)` instead of `str`, using rule's `embedding` field with fallback to default in thresher/processing/router.py
- [X] T008 [US1] Update `FileProcessor.process_file()` to use `RouteResult` from router and pass `model_name` to `MultiModelEmbedder.embed_texts()` in thresher/runner/processor.py (depends on T006, T007)
- [X] T009 [US1] Update `FileProcessor.__init__` and `ensure_collection` call to look up `vector_size`/`vector_name` from `MultiModelEmbedder.get_model_config()` in thresher/runner/processor.py (depends on T008)
- [X] T010 [US1] Update `RunnerLoop.__init__()` to create `MultiModelEmbedder` from `config.embedding.models` instead of single `Embedder` in thresher/runner/loop.py
- [X] T011 [P] [US1] Add `MultiModelEmbedder` unit tests (lazy loading, model swapping, empty texts, unknown model KeyError, preload) in tests/unit/test_embedder.py
- [X] T012 [P] [US1] Update existing `Router` tests for `RouteResult` return type and add tests for `embedding` field on rules in tests/unit/test_router.py

**Checkpoint**: Pipeline routes files to correct collections with correct embedding models. Standalone testable with `uv run pytest tests/unit/test_embedder.py tests/unit/test_router.py -v`

---

## Phase 4: User Story 2 — Custom MCP Server for Multi-Collection Search (Priority: P2)

**Goal**: MCP server selects the correct embedding model per collection at query time, so document collection searches use the text model and code collection searches use the code model.

**Independent Test**: Start the MCP server configured with both models and four collections, issue a query against a document collection, verify it uses the text model. Query a source collection, verify it uses the code model.

### Implementation for User Story 2

- [X] T013 [P] [US2] Add `CollectionConfig` Pydantic model and extend `QdrantSettings` with `collections` list and `default_collection` field in mcp-server/src/mcp_server_qdrant/settings.py
- [X] T014 [P] [US2] Add `index_prefix` and `query_prefix` constructor params to `FastEmbedProvider`, prepend `query_prefix` in `embed_query()` and `index_prefix` in `embed_documents()` in mcp-server/src/mcp_server_qdrant/embeddings/fastembed.py
- [X] T015 [US2] Update `create_embedding_provider()` to accept prefix params and add `create_collection_providers()` factory function returning `dict[str, EmbeddingProvider]` in mcp-server/src/mcp_server_qdrant/embeddings/factory.py (depends on T013, T014)
- [X] T016 [US2] Update `QdrantConnector.__init__()` to accept `embedding_providers: dict[str, EmbeddingProvider]` and route `store()`/`search()` to per-collection provider in mcp-server/src/mcp_server_qdrant/qdrant.py (depends on T015)
- [X] T017 [US2] Update `QdrantMCPServer.__init__()` to create per-collection providers when `collections` config is present, with backward-compat single-provider fallback in mcp-server/src/mcp_server_qdrant/mcp_server.py (depends on T016)
- [X] T018 [US2] Add `--config` JSON file argument to MCP server CLI entry point, load settings from JSON when provided in mcp-server/src/mcp_server_qdrant/main.py (depends on T013)
- [X] T019 [P] [US2] Add unit tests for `CollectionConfig` validation and multi-collection `QdrantSettings` in mcp-server/tests/test_settings.py
- [X] T020 [P] [US2] Add unit tests for per-collection provider routing in `QdrantConnector` in mcp-server/tests/test_multi_collection.py

**Checkpoint**: MCP server routes queries to correct embedding model per collection. Testable with `cd mcp-server && uv run pytest tests/ -v`

---

## Phase 5: User Story 5 — Nomic Task Prefix Handling (Priority: P2)

**Goal**: Nomic model's quality-critical task prefixes (`search_document: ` for indexing, `search_query: ` for querying) are applied automatically based on config, while non-prefix models are unaffected.

**Independent Test**: Embed a chunk with the pipeline using a prefix-configured model, verify prefix is prepended. Query via MCP server, verify query prefix is prepended. Verify a non-prefix model has no prefix added.

### Implementation for User Story 5

- [X] T021 [P] [US5] Add unit tests verifying `index_prefix` prepending in `MultiModelEmbedder.embed_texts()` and empty prefix passthrough in tests/unit/test_embedder.py
- [X] T022 [P] [US5] Add unit tests verifying `query_prefix` in `FastEmbedProvider.embed_query()` and `index_prefix` in `embed_documents()` with empty prefix passthrough in mcp-server/tests/test_prefix_handling.py

**Checkpoint**: Prefix behavior verified for both pipeline and MCP server paths

---

## Phase 6: User Story 3 — MCP Server Configuration Output (Priority: P3)

**Goal**: Pipeline operator can generate MCP server config from thresher pipeline config, ensuring model/collection/vector assignments match between indexing and querying.

**Independent Test**: Run `thresher mcp-config` with a multi-model config, verify JSON output contains correct Qdrant connection info, all collections with their assigned models, and resolved env var values.

### Implementation for User Story 3

- [X] T023 [US3] Implement `mcp-config` CLI subcommand in thresher/cli.py that walks routing rules to derive collection-to-model mappings and outputs JSON per contracts/interfaces.md
- [X] T024 [P] [US3] Add unit tests for `mcp-config` JSON output completeness and env var resolution in tests/unit/test_cli.py

**Checkpoint**: `thresher --config prod-config.yaml mcp-config` outputs valid MCP server configuration JSON

---

## Phase 7: User Story 4 — MCP Server as Standalone Subdirectory (Priority: P3)

**Goal**: MCP server is independently installable and testable, with CI running its checks alongside the pipeline.

**Independent Test**: Install MCP server from its subdirectory without the thresher pipeline package, run its tests, verify they pass.

### Implementation for User Story 4

- [X] T025 [US4] Add MCP server lint and test job to .github/workflows/ci.yml running ruff, mypy, and pytest in the mcp-server/ subdirectory

**Checkpoint**: CI runs MCP server tests on PRs affecting `mcp-server/` files

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Production readiness improvements that span multiple user stories

- [X] T026 [P] Update Dockerfile to pre-download both `nomic-ai/nomic-embed-text-v1.5` and `jinaai/jina-embeddings-v2-base-code` models during build
- [X] T027 [P] Update config.example.yaml with multi-model embedding and routing example
- [X] T028 Run quickstart.md validation end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup (Phase 1) completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational (Phase 2) completion — this is the MVP
- **US2 (Phase 4)**: Depends on Foundational (Phase 2) — can start in parallel with US1 (different codebase: `mcp-server/`)
- **US5 (Phase 5)**: Depends on US1 (pipeline prefix) and US2 (MCP prefix) — verification phase
- **US3 (Phase 6)**: Depends on US1 (needs `embedding.models` config parsing available)
- **US4 (Phase 7)**: Depends on US2 (needs MCP server tests to exist)
- **Polish (Phase 8)**: Depends on US1 and US2 at minimum

### User Story Dependencies

- **US1 (P1)**: After Foundational — no dependencies on other stories
- **US2 (P2)**: After Foundational — independent from US1 (separate codebase)
- **US5 (P2)**: After US1 and US2 — verifies cross-cutting prefix behavior
- **US3 (P3)**: After US1 — reads pipeline config to generate MCP config
- **US4 (P3)**: After US2 — needs MCP server test suite to exist for CI

### Within Each User Story

- Types/schema before config parsing
- Config parsing before consumer code
- Core implementation before integration points
- Unit tests can run in parallel with each other

### Parallel Opportunities

- T001 and T002 can run in parallel (different files)
- T006 and T007 can run in parallel (different files: embedder.py vs router.py)
- T011 and T012 can run in parallel (different test files)
- T013 and T014 can run in parallel (different MCP files: settings.py vs fastembed.py)
- T019 and T020 can run in parallel (different test files)
- T021 and T022 can run in parallel (different test files)
- US1 (Phase 3) and US2 (Phase 4) can run in parallel (different codebases)

---

## Parallel Example: User Story 1

```bash
# Launch parallel implementation tasks (different files):
Task T006: "Create MultiModelEmbedder in thresher/embedder.py"
Task T007: "Update Router.route() in thresher/processing/router.py"

# Then sequential integration:
Task T008: "Update FileProcessor in thresher/runner/processor.py" (needs T006 + T007)
Task T009: "Update ensure_collection in thresher/runner/processor.py" (needs T008)
Task T010: "Update RunnerLoop in thresher/runner/loop.py" (needs T006)

# Launch parallel test tasks:
Task T011: "MultiModelEmbedder tests in tests/unit/test_embedder.py"
Task T012: "Router tests in tests/unit/test_router.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T005)
3. Complete Phase 3: User Story 1 (T006–T012)
4. **STOP and VALIDATE**: `uv run pytest tests/unit/ -v && uv run prek`
5. Deploy with multi-model config and reindex

### Incremental Delivery

1. Setup + Foundational → Config infrastructure ready
2. US1 → Pipeline multi-model indexing works → Deploy and reindex (MVP!)
3. US2 → MCP server multi-collection search works → Deploy MCP server
4. US5 → Prefix behavior verified → Confidence in retrieval quality
5. US3 → Config bridge eliminates drift → Operational tooling
6. US4 → CI covers MCP server → Ongoing quality
7. Polish → Production hardening → Dockerfile, examples, docs

### Parallel Strategy

With two work streams:

1. Both complete Setup + Foundational together
2. Once Foundational is done:
   - Stream A: US1 (pipeline) → US3 (mcp-config CLI) → Polish
   - Stream B: US2 (MCP server) → US4 (CI) → Polish
3. US5 (prefix verification) after both streams merge

---

## Notes

- [P] tasks = different files, no dependencies on in-progress tasks
- [Story] label maps task to specific user story for traceability
- Each user story is independently testable at its checkpoint
- US1 and US2 are in separate codebases (root vs `mcp-server/`) and can proceed in parallel
- Commit after each task or logical group
- Run `uv run prek` after each phase to catch regressions early
