# Tasks: Thresher (Queue Runner Rebuild)

**Input**: Design documents from `/specs/005-queue-runner-rebuild/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Not explicitly requested — test tasks are omitted. Add test phases per user story if TDD is desired.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Package**: `thresher/` at repository root (new package)
- **Tests**: `tests/unit/`, `tests/integration/`, `tests/contract/`
- **Config**: `thresher/defaults.yaml` (built-in), `config.example.yaml` (user template)
- **Specs**: `specs/005-queue-runner-rebuild/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the thresher package structure and install dependencies

- [x] T001 Create thresher/ package directory structure with all subpackages (controller/, runner/, providers/, processing/, processing/extractors/, processing/chunkers/) and __init__.py files per plan.md project structure
- [x] T002 Update pyproject.toml with thresher package metadata and dependencies: docling, chonkie[code], fastembed, python-magic, google-cloud-storage, qdrant-client, kubernetes, PyYAML, plus dev dependencies (pytest)
- [x] T003 [P] Move existing src/ files to _archive/ directory and add _archive/ to .gitignore
- [x] T004 [P] Create config.example.yaml with full configuration schema per contracts/config-schema.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Provider interfaces, config system, shared types, and core utilities that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Create shared type definitions (FileInfo, IndexChunk, QueueItem, QueueBatch, ProcessingResult, ChunkerConfig, FileTypeGroup, RoutingRule, ExpansionRecord) in thresher/types.py per data-model.md
- [x] T006 [P] Define SourceProvider protocol with all methods (list_files, download_content, download_to_path, upload_content, upload_from_path, exists, delete, cache_path) in thresher/providers/source.py per contracts/source-provider.md
- [x] T007 [P] Define DestinationProvider protocol with all methods (ensure_collection, index_chunks, exists_by_hash, delete_by_source, close) in thresher/providers/destination.py per contracts/destination-provider.md
- [x] T008 Implement YAML config loading with three-layer merge (built-in defaults.yaml → user YAML → env var overrides) and validation into Config dataclass in thresher/config.py per contracts/config-schema.md and research.md R3
- [x] T009 [P] Create built-in defaults.yaml with standard file type groups (office-documents, mumps-source, mumps-globals, general-source, data-files, images, plain-text, binary) including extensions, MIME types, detectors, extractors, and chunker strategies in thresher/defaults.yaml per FR-037
- [x] T010 [P] Implement structured logging setup with file paths, processing times, status, and memory usage fields in thresher/logging_config.py per FR-030
- [x] T011 [P] Implement FastEmbed ONNX embedding wrapper (sentence-transformers/all-MiniLM-L6-v2, 384 dims) in thresher/embedder.py

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Full Pipeline Run with Queue Architecture (Priority: P1) 🎯 MVP

**Goal**: Controller scans GCS, builds queue of individual files; runners claim batches, process one file at a time through classify → extract → chunk → index, mark complete. This is the core architectural change.

**Independent Test**: Run controller to build queue from a small GCS prefix, then run a single runner that processes all queued items. Verify files are classified, extracted, chunked, and indexed into Qdrant with correct metadata.

### Implementation for User Story 1

- [x] T012 [P] [US1] Implement GCS source provider (list_files, download_content, download_to_path, upload_content with if_generation_match, upload_from_path, exists, delete, cache_path) wrapping google.cloud.storage.Client in thresher/providers/gcs.py per contracts/source-provider.md
- [x] T013 [P] [US1] Implement Qdrant destination provider (ensure_collection with named vectors, index_chunks with batch upsert, exists_by_hash, delete_by_source, close) wrapping qdrant_client.QdrantClient in thresher/providers/qdrant.py per contracts/destination-provider.md
- [x] T014 [P] [US1] Implement file classifier with extension matching, MIME type detection via python-magic, and priority-ordered file type group resolution in thresher/processing/classifier.py per FR-014
- [x] T015 [P] [US1] Implement routing engine with basic rule evaluation (file_group + path + filename criteria, AND/OR semantics, first-match-wins) and default collection fallback in thresher/processing/router.py per FR-027/FR-028
- [x] T016 [P] [US1] Implement raw-text extractor (read bytes, decode as text) in thresher/processing/extractors/raw_text.py per FR-015
- [x] T017 [P] [US1] Implement docling subprocess-isolated extractor with configurable timeout (300s default), temp file communication, and dual-cache check (.md + .docling.json) in thresher/processing/extractors/docling.py per FR-015/FR-016 and research.md R5
- [x] T018 [US1] Implement docling HybridChunker wrapper for docling-extracted documents in thresher/processing/chunkers/docling_hybrid.py per FR-017
- [x] T018a [P] [US1] Implement MUMPS label-boundary chunker (label pattern detection, subroutine-aware splitting) in thresher/processing/chunkers/mumps_label.py per FR-017 — needed for MVP to process MUMPS source files
- [x] T018b [P] [US1] Implement Chonkie RecursiveChunker wrapper (from_recipe for markdown, configurable min_characters_per_chunk) in thresher/processing/chunkers/chonkie_recursive.py per FR-017 and research.md R1 — needed for MVP to process plain text and data files
- [x] T018c [US1] Implement config-driven chunker dispatch — resolve chunker strategy from file type group config, instantiate correct chunker with strategy-specific params in thresher/runner/processor.py per FR-017
- [x] T018d [US1] Implement provider factory dispatch — instantiate correct source/destination provider based on config (`source.provider: gcs` → GCSSourceProvider, `destination.provider: qdrant` → QdrantDestinationProvider) in thresher/config.py per FR-040
- [x] T019 [P] [US1] Implement URL resolver for source URL reconstruction (httrack mirror comments, WorldVistA GitHub mapping, domain-first path reconstruction) in thresher/url_resolver.py per FR-033
- [x] T020 [US1] Implement controller file scanner (list source provider files with prefix filtering, classify to exclude non-indexable binary files early) in thresher/controller/scanner.py per FR-001/FR-006
- [x] T021 [US1] Implement queue builder (partition files into batch JSON files of configurable size, write to queue/pending/ on source provider) in thresher/controller/queue_builder.py per FR-021 and contracts/queue-batch.schema.json
- [x] T022 [US1] Implement runner processor (single-file pipeline: classify → extract → chunk → embed → index with correct metadata payload) in thresher/runner/processor.py per FR-009/FR-018/FR-019
- [x] T023 [US1] Implement runner main loop (claim batch via atomic GCS conditional create, process items one at a time, mark complete/failed, move batch to queue/done/) in thresher/runner/loop.py per FR-008/FR-010/FR-011/FR-012 and research.md R4
- [x] T024 [US1] Implement CLI entrypoint with controller and runner subcommands, --config flag, --runner-id flag in thresher/cli.py per FR-045
- [x] T025 [US1] Create __main__.py entry point (python -m thresher) delegating to cli.py in thresher/__main__.py

**Checkpoint**: Core pipeline functional — controller builds queue, single runner processes all files end-to-end

---

## Phase 4: User Story 2 — Failure Resilience and Retry (Priority: P1)

**Goal**: Crashed runners don't block progress; failed files retry up to 3 times then move to permanent failure; runners self-monitor memory and exit gracefully before OOM.

**Independent Test**: Simulate runner crash mid-file, verify item returns to queue and is picked up by another runner. After 3 failures, verify permanent failure state. Test memory threshold exit.

### Implementation for User Story 2

- [x] T026 [US2] Implement retry logic with attempt tracking per queue item — failed items written to queue/retry/ for subsequent retry pass in thresher/runner/loop.py per FR-022/FR-023
- [x] T027 [US2] Implement stale batch reclaim — scan queue/claimed/, detect batches where claimed_at + lease_timeout < now, move back to queue/pending/ using atomic conditional create in thresher/runner/loop.py per FR-025
- [x] T028 [P] [US2] Implement RSS memory monitor with configurable threshold (default 4 GB), finish current file then graceful exit in thresher/runner/memory_monitor.py per FR-013
- [x] T029 [US2] Add per-file processing timeout enforcement (default 600s) — terminate and mark failed on timeout in thresher/runner/processor.py per FR-036
- [x] T030 [US2] Implement permanent failure handling — items exceeding retry_max (default 3) written to queue/failed/ with last error reason in thresher/runner/loop.py per FR-024
- [x] T031 [US2] Add summary reporting at end of runner execution (total files, processed, skipped, failed, permanently failed counts) in thresher/runner/loop.py per FR-031
- [x] T032 [P] [US2] Add Linux memory optimizations: malloc_trim via ctypes, set MALLOC_ARENA_MAX=2, gc.collect() between files in thresher/runner/memory_monitor.py per FR-034

**Checkpoint**: Pipeline resilient to crashes and OOM — automatic retry, permanent failure tracking, memory-safe runners

---

## Phase 5: User Story 3 — Configurable Collection Routing (Priority: P2)

**Goal**: Operators configure YAML routing rules to direct files to different Qdrant collections based on file type groups and path patterns. File type groups are first-class config objects defining membership, extraction, and chunking.

**Independent Test**: Configure routing rules in YAML, run a small set of files, verify each lands in the correct Qdrant collection based on its file type group and path.

### Implementation for User Story 3

- [x] T033 [US3] Extend classifier with custom content detectors (MUMPS label patterns, caret density analysis) registered by name in file type group config in thresher/processing/classifier.py per FR-014
- [x] T034 [US3] Implement file type group merge logic — user-defined groups with same name completely replace built-in; unmentioned built-in groups preserved in thresher/config.py per FR-026
- [x] T035 [US3] Enhance routing rules engine with full AND/OR criteria evaluation, regex path patterns, filename glob patterns, and declaration-order first-match-wins in thresher/processing/router.py per FR-027
- [x] T036 [US3] Add default source-code routing (auto-append source_suffix to collection name for source-code file type groups when no explicit rule matches) in thresher/processing/router.py per FR-027
- [x] T037 [US3] Add image size threshold check — skip images below configurable minimum (default 50 KB) from docling OCR processing in thresher/processing/classifier.py per FR-014

**Checkpoint**: Routing fully config-driven — new projects can be configured without code changes

---

## Phase 6: User Story 4 — Archive Expansion to GCS (Priority: P2)

**Goal**: Controller expands ZIP/TAR archives to a separate GCS folder, queuing individual member files for runners. Runners never handle archives directly.

**Independent Test**: Place archives in GCS bucket, run controller, verify individual files appear in expanded/ GCS folder and are queued for processing.

### Implementation for User Story 4

- [x] T038 [US4] Implement archive expander supporting ZIP, TAR, GZ, BZ2, XZ formats — download archive, extract members, upload individual files to configured expansion folder on source provider in thresher/controller/archive_expander.py per FR-002
- [x] T039 [US4] Add recursive archive expansion with configurable max depth (default 2) for nested archives in thresher/controller/archive_expander.py per FR-007
- [x] T040 [US4] Implement idempotent expansion with expansion records (JSON on source provider) — skip re-expansion of previously expanded archives in thresher/controller/archive_expander.py per FR-003
- [x] T041 [US4] Add hidden file and resource fork filtering — skip __MACOSX, ._* files, Thumbs.db, desktop.ini, and non-extractable archive formats (.jar, .war, .whl, .egg) within archives in thresher/controller/archive_expander.py per FR-035
- [x] T042 [US4] Integrate archive expansion into controller scan workflow — expand archives before queue building, include expanded files in queue in thresher/controller/scanner.py per FR-004

**Checkpoint**: Archives transparently expanded — runners only process individual files

---

## Phase 7: User Story 5 — Skip List and Incremental Processing (Priority: P2)

**Goal**: Re-runs skip already-processed files via skip list and content-hash dedup. Force flag overrides for full reprocessing.

**Independent Test**: Run pipeline once, add new files, re-run and verify only new files processed. Re-run with force flag and verify all files reprocessed.

### Implementation for User Story 5

- [x] T043 [US5] Implement skip list management on source provider — read existing skip list, update after successful processing, use during queue building to exclude completed files in thresher/controller/scanner.py per FR-005
- [x] T044 [US5] Add content-hash dedup check (SHA256 truncated to 32 hex chars) via destination provider exists_by_hash before indexing — skip files with matching hash in thresher/runner/processor.py per FR-019
- [x] T045 [US5] Add --force flag to CLI and propagate through config to bypass skip list exclusion and content-hash dedup in thresher/cli.py per FR-005/FR-019
- [x] T046 [US5] Implement deterministic point ID generation (UUID5 from source_path + chunk_index) for idempotent upserts in thresher/runner/processor.py per FR-019 and contracts/destination-provider.md

**Checkpoint**: Incremental processing works — only new/changed files processed on re-runs

---

## Phase 8: User Story 6 — Extensible Chunking Strategy (Priority: P3)

**Goal**: Complete the chunking strategy set with Chonkie CodeChunker (tree-sitter AST) and add chunker-specific metadata to indexed payloads. Docling-hybrid, MUMPS label-boundary, and Chonkie recursive are already implemented in Phase 3.

**Independent Test**: Process files of different types (MUMPS, PDF, Python source, plain text) and verify each uses the appropriate chunking strategy with correct output including chunker-specific metadata.

### Implementation for User Story 6

- [x] T047 [P] [US6] Implement Chonkie CodeChunker wrapper (tree-sitter AST-based, language hint support, 165+ languages) in thresher/processing/chunkers/chonkie_code.py per FR-017 and research.md R1
- [x] T048 [US6] Register chonkie-code strategy in chunker dispatch and update defaults.yaml general-source group to use it in thresher/runner/processor.py and thresher/defaults.yaml
- [x] T049 [US6] Add chunker-specific metadata to index payloads — headings for docling-hybrid, start_line/end_line for code chunkers (derived from character offsets) in thresher/runner/processor.py per FR-018

**Checkpoint**: All four chunking strategies operational and config-driven

---

## Phase 9: User Story 7 — Docker Image and K8s Job Orchestration (Priority: P2)

**Goal**: Single Docker image with controller/runner entrypoints. Controller creates runner K8s Jobs via K8s API or exports manifests. Supports local, k8s-deploy, and manifest-export modes.

**Independent Test**: Run controller with --k8s-deploy against a test cluster, verify it creates correct runner Jobs. Test manifest export with --k8s-manifest-out.

### Implementation for User Story 7

- [x] T052 [P] [US7] Create Dockerfile with all dependencies (docling, chonkie with tree-sitter, fastembed, python-magic, K8s client), dual entrypoints for controller and runner in Dockerfile per FR-041
- [x] T053 [US7] Implement K8s Job orchestrator — create runner Jobs via kubernetes Python client with resource limits, config mounts, credentials, unique runner IDs, labels in thresher/controller/k8s_orchestrator.py per FR-042/FR-043 and research.md R2
- [x] T054 [US7] Add manifest export mode — serialize Job specs to YAML via ApiClient.sanitize_for_serialization, write to file specified by --k8s-manifest-out in thresher/controller/k8s_orchestrator.py per FR-044
- [x] T055 [US7] Implement --local mode (embedded runner inline after queue building) and wire three mutually exclusive execution modes (--local, --k8s-deploy, --k8s-manifest-out, queue-only default) in thresher/cli.py per FR-045
- [x] T056 [US7] Add kubernetes configuration section loading (namespace, service_account, image, image_pull_policy, resources, max_parallelism, node_selector, tolerations, backoff_limit, ttl) in thresher/config.py per FR-046
- [x] T057 [US7] Implement self-referencing image detection from pod metadata and namespace auto-discovery from service account path in thresher/controller/k8s_orchestrator.py per FR-046 and research.md R2

**Checkpoint**: Full CI-to-processing pipeline operational — controller self-orchestrates runner Jobs

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T058 [P] Add dry-run mode to controller — build queue and report what would be processed without enqueuing in thresher/controller/scanner.py per FR-032
- [x] T059 [P] Add file size threshold enforcement — max_file_size (50 MB) for documents and max_source_size (10 MB) for source code, skip with warnings in thresher/runner/processor.py per FR-020
- [x] T060 [P] Add controller summary reporting (total files scanned, queued, skipped, batches created) in thresher/controller/queue_builder.py per FR-031
- [x] T061 Run quickstart.md validation scenarios end-to-end — include URL reconstruction accuracy spot-check (FR-033: httrack, WorldVistA GitHub, domain-first paths)
- [x] T062 Verify SC-001 (coverage parity): run full corpus, compare indexed source paths between old and new Qdrant collections, confirm ≥99% coverage
- [x] T063 Verify SC-004 (performance parity): benchmark full corpus run at equal parallelism, confirm ≤120% of current system wall-clock time
- [x] T064 Verify SC-007 (throughput): measure files processed per runner pod lifecycle for small files (<1 MB), confirm ≥100 files/pod
- [x] T065 Verify SC-005 (incremental processing): run pipeline, add new files, re-run and confirm <1% duplicate processing; verify force flag reprocesses all

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — core pipeline, must complete before US2
- **US2 (Phase 4)**: Depends on US1 (extends runner loop and processor)
- **US3 (Phase 5)**: Depends on Phase 2 — extends classifier and router. Can start new-file tasks (T033 detector framework, T034 merge logic) in parallel with US1 after foundational, but integration tasks (T035, T036) depend on US1's router (T015)
- **US4 (Phase 6)**: Depends on Phase 2 — extends controller scanner. Can start archive expander tasks (T038-T041) in parallel with US1 after foundational, but integration task (T042) depends on US1's scanner (T020)
- **US5 (Phase 7)**: Depends on US1 — extends scanner and processor with skip/dedup logic
- **US6 (Phase 8)**: Depends on US1 — adds chunker implementations to existing processor
- **US7 (Phase 9)**: Depends on US1 — adds K8s orchestration to controller and CLI
- **Polish (Phase 10)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: After Foundational — No dependencies on other stories. **MVP target.**
- **US2 (P1)**: After US1 — Extends runner loop with resilience. Critical for production use.
- **US3 (P2)**: After Foundational — New-file tasks can start after Phase 2; integration tasks (T035, T036) depend on US1's router
- **US4 (P2)**: After Foundational — Archive expander tasks can start after Phase 2; integration task (T042) depends on US1's scanner
- **US5 (P2)**: After US1 — Needs working scanner and processor to add incremental behavior
- **US6 (P3)**: After US1 — Needs working processor to add chunker dispatch
- **US7 (P2)**: After US1 — Needs working CLI and controller to add K8s orchestration

### Recommended Execution Order (Sequential)

1. Phase 1 → Phase 2 → **Phase 3 (US1)** → validate MVP
2. **Phase 4 (US2)** → validate resilience
3. Phase 5 (US3) + Phase 6 (US4) → validate routing + archives
4. Phase 7 (US5) → validate incremental processing
5. Phase 8 (US6) → validate all chunking strategies
6. Phase 9 (US7) → validate K8s deployment
7. Phase 10 → final validation

### Within Each User Story

- Models/types before services
- Provider implementations before consumers
- Core logic before CLI integration
- Sequential within a story unless marked [P]

### Parallel Opportunities

**Phase 1**: T003 + T004 can run in parallel

**Phase 2**: T006 + T007 in parallel (provider protocols); T009 + T010 + T011 in parallel (defaults, logging, embedder)

**Phase 3 (US1)**: T012 + T013 in parallel (GCS + Qdrant providers); T014 + T015 + T016 + T017 + T018a + T018b + T019 in parallel (classifier, router, extractors, chunkers, URL resolver); T024 + T025 after loop is complete

**Phase 4 (US2)**: T028 + T032 in parallel (memory monitor tasks)

**Phase 6 (US4)**: T038 first, then T039 + T040 + T041 can overlap

**Phase 8 (US6)**: T047 first (CodeChunker impl), then T048 → T049 sequential (dispatch registration → metadata)

**Phase 10**: T058 + T059 + T060 all in parallel

---

## Parallel Example: User Story 1

```bash
# After Phase 2 is complete, launch provider implementations together:
Task T012: "Implement GCS source provider in thresher/providers/gcs.py"
Task T013: "Implement Qdrant destination provider in thresher/providers/qdrant.py"

# Then launch classifier, router, extractors, URL resolver together:
Task T014: "Implement file classifier in thresher/processing/classifier.py"
Task T015: "Implement routing engine in thresher/processing/router.py"
Task T016: "Implement raw-text extractor in thresher/processing/extractors/raw_text.py"
Task T017: "Implement docling subprocess extractor in thresher/processing/extractors/docling.py"
Task T018a: "Implement MUMPS label-boundary chunker in thresher/processing/chunkers/mumps_label.py"
Task T018b: "Implement Chonkie RecursiveChunker wrapper in thresher/processing/chunkers/chonkie_recursive.py"
Task T019: "Implement URL resolver in thresher/url_resolver.py"

# Then sequential: docling hybrid chunker → chunker dispatch → provider factory → scanner → queue → processor → loop → CLI
Task T018 → T018c → T018d → T020 → T021 → T022 → T023 → T024 → T025
```

## Parallel Example: User Story 6

```bash
# Chonkie CodeChunker is the only new chunker in Phase 8
# (MUMPS label-boundary and Chonkie recursive already implemented in Phase 3)
Task T047: "Implement Chonkie CodeChunker wrapper in thresher/processing/chunkers/chonkie_code.py"

# Then sequential: register in dispatch → metadata
Task T048 → T049
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (full pipeline)
4. **STOP and VALIDATE**: Run controller + single runner against small GCS prefix
5. Deploy/demo if ready — this is the core architectural improvement

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. **US1** → Core pipeline working → **MVP deployed** ✅
3. **US2** → Resilience added → Production-ready for existing files
4. **US3 + US4** → Routing + archives → Full corpus coverage
5. **US5** → Incremental processing → Efficient re-runs
6. **US6** → All chunking strategies → Best-quality output
7. **US7** → K8s orchestration → Automated deployment
8. Polish → Dry-run, size thresholds, summary reporting

### Parallel Team Strategy

With multiple developers after Phase 2:

- **Developer A**: US1 (core pipeline) → US2 (resilience) → US5 (skip list)
- **Developer B**: US3 (routing config) → US6 (chunking strategies)
- **Developer C**: US4 (archive expansion) → US7 (K8s orchestration)

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks in same phase
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable after its dependencies
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All provider interactions go through SourceProvider/DestinationProvider protocols — never call GCS/Qdrant directly
- File type groups are the central abstraction — membership + extractor + chunker per group
- Queue batches use GCS atomic conditional create (`if_generation_match=0`) for contention-free claiming
