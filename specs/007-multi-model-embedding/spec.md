# Feature Specification: Multi-Model Embedding with Custom MCP Server

**Feature Branch**: `007-multi-model-embedding`
**Created**: 2026-03-29
**Status**: Draft
**Input**: User description: "Multi-model embedding support with per-collection models and custom MCP server"

## User Scenarios & Testing

### User Story 1 - Per-Collection Embedding Models (Priority: P1)

A pipeline operator configures thresher to use different embedding models for different collections. Document collections (vista, rpms) are embedded with a text-optimized model that understands healthcare IT documentation. Source code collections (vista-source, rpms-source) are embedded with a code-aware model that handles MUMPS syntax, terse abbreviations, and mixed-language codebases. When the pipeline runs, each file is embedded with the model assigned to its target collection.

**Why this priority**: This is the core capability — without per-collection model routing, all downstream features are blocked. It directly improves retrieval quality for the two distinct content domains.

**Independent Test**: Configure two embedding models in YAML, process one document file and one source code file, verify each is embedded with the correct model and indexed into the correct collection with the correct vector name.

**Acceptance Scenarios**:

1. **Given** a config with a "docs" model and a "code" model mapped to routing rules, **When** a PDF file routes to the "vista" collection, **Then** it is embedded using the docs model and stored with the docs vector name
2. **Given** the same config, **When** a .m MUMPS file routes to "vista-source", **Then** it is embedded using the code model and stored with the code vector name
3. **Given** a routing rule with no explicit embedding assignment, **When** a file matches that rule, **Then** the default embedding model is used
4. **Given** existing collections with vectors from the old single-model config, **When** reindexing with the new multi-model config, **Then** collections are recreated with the correct named vectors for their assigned model

---

### User Story 2 - Custom MCP Server for Multi-Collection Search (Priority: P2)

A developer uses Claude Desktop or another MCP client to search the VistA/RPMS knowledge base. The MCP server accepts a query and determines which collection to search based on explicit collection selection. For document collections, the query is embedded with the text model; for source code collections, it is embedded with the code model. Results are returned with rich metadata including source URLs and collection provenance.

**Why this priority**: The MCP server is the primary consumer of the indexed data. Without query-time model routing, searches against code collections would use the wrong embedding model, producing poor results.

**Independent Test**: Start the MCP server, issue a natural-language query, verify it returns results from document collections. Issue a code-related query specifying a source collection, verify it uses the code model and returns relevant MUMPS routines.

**Acceptance Scenarios**:

1. **Given** a running MCP server configured with both models and four collections, **When** a user searches a document collection, **Then** the query is embedded with the text model and results contain relevant documents
2. **Given** the same server, **When** a user searches a source-code collection, **Then** the query is embedded with the code model and results contain relevant code chunks
3. **Given** the server has multiple collections configured, **When** a user does not specify a collection, **Then** the server searches the default collection
4. **Given** search results, **When** results are returned, **Then** each result includes source path, source URL, content, and collection name in structured format

---

### User Story 3 - MCP Server Configuration Output (Priority: P3)

A pipeline operator runs a thresher command that outputs the MCP server configuration derived from the thresher pipeline config. This ensures the MCP server uses the same models, vector names, Qdrant connection, and collection mappings as the indexing pipeline, eliminating configuration drift.

**Why this priority**: Prevents the common error of mismatched embedding models between indexing and querying, which produces silently degraded search results.

**Independent Test**: Run the config output command with a thresher config file, verify the output contains correct Qdrant connection info, collection-to-model mappings, and vector names matching what the pipeline would use.

**Acceptance Scenarios**:

1. **Given** a thresher config with multi-model embeddings, **When** the operator runs the config output command, **Then** a complete MCP server configuration is produced that matches the pipeline's model/collection/vector assignments
2. **Given** the output configuration, **When** it is used to start the MCP server, **Then** the server connects to the same Qdrant instance and uses the same vector names as the pipeline
3. **Given** a thresher config with environment variable overrides (QDRANT_URL, QDRANT_API_KEY), **When** the config output command runs, **Then** the resolved values are included in the output

---

### User Story 4 - MCP Server as Standalone Subdirectory (Priority: P3)

A developer can work on the MCP server independently from the thresher pipeline. The MCP server lives in a subdirectory with its own pyproject.toml, lock file, and virtual environment. CI workflows in the thresher repository run MCP server tests and linting alongside the pipeline tests.

**Why this priority**: Maintains separation of concerns while enabling integrated CI. The MCP server has different runtime dependencies (FastMCP, async Qdrant client) than the pipeline.

**Independent Test**: Navigate to the MCP server subdirectory, install its dependencies independently, and run its tests without the thresher pipeline installed.

**Acceptance Scenarios**:

1. **Given** the MCP server subdirectory, **When** a developer runs its test suite, **Then** all tests pass without requiring the parent thresher package
2. **Given** the thresher CI pipeline, **When** a PR modifies MCP server code, **Then** MCP server linting and tests run as part of the CI checks
3. **Given** the MCP server subdirectory, **When** a developer installs it via pip/uv, **Then** the `mcp-server-qdrant` CLI entrypoint is available

---

### User Story 5 - Nomic Task Prefix Handling (Priority: P2)

The nomic-embed-text-v1.5 model requires task-specific prefixes: `search_document: ` for indexing and `search_query: ` for querying. The pipeline automatically prepends the indexing prefix when embedding chunks. The MCP server automatically prepends the query prefix when embedding search queries. Other models (like jina-embeddings-v2-base-code) that do not require prefixes are not affected.

**Why this priority**: Without correct prefixes, nomic embeddings are degraded by 10-15% in retrieval accuracy. This is a correctness requirement, not an enhancement.

**Independent Test**: Embed a document chunk with the pipeline and verify the indexing prefix is prepended. Query via the MCP server and verify the query prefix is prepended. Verify a non-prefix model is not affected.

**Acceptance Scenarios**:

1. **Given** a model configured with an indexing prefix, **When** a chunk is embedded during pipeline processing, **Then** the prefix is prepended to the text before embedding
2. **Given** a model configured with a query prefix, **When** a search query is embedded by the MCP server, **Then** the prefix is prepended to the query before embedding
3. **Given** a model with no prefix configuration, **When** text is embedded, **Then** no prefix is added

---

### Edge Cases

- What happens when a collection's assigned model is not available (download failure or misconfigured name)? The pipeline should fail fast with a clear error at model preload time.
- What happens when the config references an embedding name not defined in the models map? Startup validation should reject the config with a descriptive error.
- What happens when the pipeline encounters a file that routes to a collection whose model hasn't been preloaded yet? The embedder should lazy-load it on first use.
- How does the MCP server handle a search request for a collection whose model failed to load? Return an MCP error to the client with details.
- What happens with the legacy single-model `embedding` block? Treated as the sole default model for backward compatibility.

## Requirements

### Functional Requirements

**Thresher Pipeline — Multi-Model Embedding**

- **FR-001**: System MUST support defining multiple named embedding models in the configuration, each with model identifier, vector size, vector name, max tokens, and optional indexing/query prefix settings
- **FR-002**: System MUST support assigning an embedding model name to routing rules, with a configurable default model for rules without an explicit assignment
- **FR-003**: When processing a file, system MUST select the embedding model based on the routing rule that matched the file
- **FR-004**: Each Qdrant collection MUST be created with the vector name and size matching its assigned embedding model
- **FR-005**: Embedder MUST support lazy-loading of models, loading each model only when first needed, to avoid loading all models into memory simultaneously
- **FR-006**: When a model has a configured indexing prefix, system MUST prepend it to chunk text before embedding during pipeline processing
- **FR-007**: System MUST maintain backward compatibility with the existing single-model `embedding` configuration block, treating it as the default model when no `embedding.models` map is present
- **FR-008**: System MUST validate at startup that all embedding names referenced in routing rules exist in the models map

**MCP Server — Multi-Collection Search**

- **FR-009**: MCP server MUST support configuring multiple collections, each with its own embedding model, vector name, and vector size
- **FR-010**: When searching a collection, MCP server MUST embed the query using the model assigned to that collection
- **FR-011**: When a model has a configured query prefix, MCP server MUST prepend it to the query text before embedding
- **FR-012**: MCP server MUST expose a `qdrant-find` tool that accepts a collection name parameter and searches the specified collection with the correct embedding model
- **FR-013**: MCP server MUST expose a `qdrant-store` tool for storing content (preserving upstream compatibility) when not in read-only mode
- **FR-014**: MCP server MUST return search results with source path, source URL, content text, and collection name in each result

**Configuration Bridge**

- **FR-015**: Thresher CLI MUST provide a subcommand that outputs MCP server configuration derived from the pipeline's config, including Qdrant connection details, collection-to-model mappings, vector names, and prefix settings
- **FR-016**: Output configuration MUST resolve environment variable overrides before output

**Project Structure**

- **FR-017**: MCP server MUST reside in a subdirectory of the thresher repository with its own pyproject.toml and dependency lock file
- **FR-018**: GitHub Actions workflows in the thresher repository MUST include CI steps for MCP server linting and testing
- **FR-019**: MCP server MUST be installable and testable independently from the thresher pipeline package

### Key Entities

- **Embedding Model Config**: A named configuration specifying a model identifier, vector dimensions, vector name for Qdrant, max token length, and optional indexing/query prefixes
- **Collection-Model Mapping**: The association between a Qdrant collection (determined by routing rules) and its assigned embedding model configuration
- **MCP Server Config**: Configuration for the MCP server including Qdrant connection, collection definitions with their embedding models, vector names, and tool customization settings

## Success Criteria

### Measurable Outcomes

- **SC-001**: Document collection searches return measurably more relevant results compared to the current single-model baseline (validated by reviewing sample queries against known-good documents)
- **SC-002**: Source code collection searches return MUMPS routines and code chunks that match the intent of natural-language queries about code functionality
- **SC-003**: Pipeline successfully indexes 700,000 files using per-collection models without errors caused by model mismatch or vector dimension conflicts
- **SC-004**: MCP server starts and responds to search queries within 5 seconds of receiving them (excluding initial model load)
- **SC-005**: Pipeline operator can generate MCP server configuration and start the server without manually duplicating any settings from the pipeline config
- **SC-006**: All existing pipeline unit tests continue to pass with the single-model backward-compatible configuration
- **SC-007**: MCP server test suite passes independently when run from its subdirectory

## Assumptions

- The two selected models are `nomic-ai/nomic-embed-text-v1.5` (768 dimensions, for documents) and `jinaai/jina-embeddings-v2-base-code` (768 dimensions, for source code); both are Apache 2.0 licensed and fastembed-compatible
- Nomic v1.5 requires `search_document: ` prefix for indexing and `search_query: ` prefix for querying; Jina v2-code does not require prefixes
- Both models fit within the runner pod's 8Gi memory request alongside docling models, since only one model is loaded at a time (lazy loading) and each is approximately 550MB unquantized
- The existing GCS extraction cache (`.md` and `.docling.json` files) will be reused during reindexing — only embedding and indexing need to be redone
- The MCP server will use `fastembed` for local embeddings, matching the pipeline's embedding backend
- The MCP server's existing upstream tool interface (`qdrant-find`, `qdrant-store`) will be preserved with additive changes for multi-collection and per-collection model selection
- Qdrant collections will use a single named vector per collection (not multiple named vectors per collection), since document and code collections are already separated by routing rules
- The Dockerfile build process will pre-download both embedding models to avoid runtime downloads in K8s pods
