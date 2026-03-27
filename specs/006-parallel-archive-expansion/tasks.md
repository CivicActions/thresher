# Tasks: Parallel Archive Expansion & Batched Uploads

**Input**: Design documents from `/specs/006-parallel-archive-expansion/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/expansion-job.md, research.md, quickstart.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Config & Types)

**Purpose**: Add configuration fields and data types needed by all user stories

- [X] T001 Add `max_expansion_parallelism` (int, default 5), `upload_batch_size` (int, default 50), and `expansion_timeout` (int, default 3600) fields to `ProcessingConfig` dataclass in `thresher/config.py`
- [X] T002 [P] Add JSON Schema definitions for the three new processing fields (integer type, minimum 1, descriptions) to the `processing` section of `thresher/config_schema.json`
- [X] T003 [P] Add `max_expansion_parallelism: 5`, `upload_batch_size: 50`, `expansion_timeout: 3600` under `processing:` in `thresher/defaults.yaml`
- [X] T004 [P] Add `ExpansionResult` dataclass (archives_expanded, archives_failed, files_extracted, duration_seconds, failed_archives) to `thresher/types.py` per data-model.md
- [X] T005 Update unit tests for new config fields: add validation tests in `tests/unit/test_config.py` and schema tests in `tests/unit/test_config_schema.py`

**Checkpoint**: Config loads and validates new fields; `ExpansionResult` importable from types

---

## Phase 2: Foundational (Scanner Refactoring)

**Purpose**: Split `scan_files()` so the controller can insert an expansion phase between scanning and queue building

**⚠️ CRITICAL**: US1 and US3 cannot proceed until the scanner is split

- [ ] T006 Refactor `scan_files()` in `thresher/controller/scanner.py` into `scan_direct_files(source, config)` (returns direct file dicts + archive FileInfo list) and `scan_expanded_files(source, config)` (scans expanded prefix, returns expanded file dicts). Keep `scan_files()` as a backward-compatible wrapper that calls both.
- [ ] T007 Update scanner tests in `tests/unit/test_controller.py` for split methods: test `scan_direct_files()` returns archives separately, test `scan_expanded_files()` returns expanded items, test `scan_files()` wrapper still works
- [ ] T008 [P] Add `max_expansion_parallelism`, `upload_batch_size`, and `expansion_timeout` fields with comments to `config.example.yaml`

**Checkpoint**: `scan_direct_files()` and `scan_expanded_files()` work independently; existing `scan_files()` behavior unchanged

---

## Phase 3: User Story 1 — Parallel Archive Expansion via Jobs (Priority: P1) 🎯 MVP

**Goal**: Archive expansion runs in parallel across K8s Jobs (one per archive) or local threads, controlled by `max_expansion_parallelism`

**Independent Test**: Upload 10 ZIP archives to source bucket. Run controller. Verify multiple expansion jobs run concurrently and all archives expand before processing begins.

### Implementation for User Story 1

- [ ] T009 [US1] Create `thresher/controller/expansion_orchestrator.py` with `ExpansionOrchestrator.__init__(config, source)` and `expand_local(archives: list[FileInfo]) -> ExpansionResult` that runs `ArchiveExpander._expand_single()` per archive in a `ThreadPoolExecutor(max_workers=max_expansion_parallelism)`, collecting results into `ExpansionResult`
- [ ] T010 [US1] Add `build_expansion_job_specs(archive_paths: list[str]) -> list[dict]` to `K8sOrchestrator` in `thresher/controller/k8s_orchestrator.py` that builds one K8s Job spec per archive path per the template in `contracts/expansion-job.md` (job name: `thresher-expander-{archive_stem}`, args: `["expander", "--config", ...]`, labels: `component: expander`)
- [ ] T011 [US1] Add `expander` subcommand to `thresher/cli.py` with `--config`, `--archive-path`, and `--force` args per `contracts/expansion-job.md`: load config, create source, check expansion record, call `ArchiveExpander` for the single archive, exit 0/1
- [ ] T012 [US1] Add `expand_k8s(archives: list[FileInfo]) -> ExpansionResult` to `ExpansionOrchestrator` in `thresher/controller/expansion_orchestrator.py`: deploy expansion jobs via `K8sOrchestrator.build_expansion_job_specs()` + `deploy_jobs()`, poll for completion by checking expansion records on GCS + K8s Job status, respect `max_expansion_parallelism` and `expansion_timeout`
- [ ] T013 [US1] Add idempotency logic to `ExpansionOrchestrator.expand_local()` and `expand_k8s()`: before dispatching, check for existing expansion records and skip already-expanded archives; log skipped count
- [ ] T014 [P] [US1] Create `tests/unit/test_expansion_orchestrator.py` with tests: local expansion dispatches to thread pool, K8s expansion builds correct job specs, idempotency skips expanded archives, failed archives collected in ExpansionResult, timeout raises appropriate error
- [ ] T015 [P] [US1] Add expansion job spec tests to `tests/unit/test_k8s_orchestrator.py`: verify job name format, container args include archive path, labels include `component: expander`, backoffLimit is 1

**Checkpoint**: `thresher expander --archive-path X` works standalone; `ExpansionOrchestrator.expand_local()` parallelizes across archives; K8s job specs generated correctly

---

## Phase 4: User Story 2 — Batched GCS Uploads During Expansion (Priority: P2)

**Goal**: Expanded archive members upload concurrently (up to `upload_batch_size` threads) instead of one at a time

**Independent Test**: Expand a ZIP with 500 files. Verify concurrent upload groups and all files present in GCS after completion.

### Implementation for User Story 2

- [ ] T016 [US2] Add `_upload_batch(files: list[tuple[str, str]], max_workers: int)` method to `ArchiveExpander` in `thresher/controller/archive_expander.py` using `concurrent.futures.ThreadPoolExecutor` to upload extracted members concurrently; integrate into `_expand_single()` replacing the sequential upload loop; read `upload_batch_size` from config
- [ ] T017 [US2] Add per-file retry with exponential backoff (base 1s, max 3 retries per research.md R1) inside `_upload_batch()` in `thresher/controller/archive_expander.py`; collect and re-raise if all retries exhausted for any file
- [ ] T018 [P] [US2] Update `tests/unit/test_archive_expander.py` with tests: concurrent upload uses ThreadPoolExecutor with configured batch size, failed upload retries with backoff, partial batch failure retries then raises, single large file uploads normally

**Checkpoint**: Archive expansion uploads files concurrently; failed uploads retry; all existing archive expander tests still pass

---

## Phase 5: User Story 3 — Expansion Queue Coordination (Priority: P3)

**Goal**: Controller orchestrates expand-then-process: expansion completes before queue batches are built, ensuring all expanded files are included

**Independent Test**: Run controller with archives + direct files. Verify processing batches include both direct and all expanded archive members.

### Implementation for User Story 3

- [ ] T019 [US3] Integrate expansion phase into controller command handler in `thresher/cli.py`: after `scan_direct_files()`, if archives found, call `ExpansionOrchestrator.expand_local()` (for `--local`) or `expand_k8s()` (for `--k8s-deploy`), then call `scan_expanded_files()`, merge results, build queue batches from combined list
- [ ] T020 [US3] Add structured progress logging to `ExpansionOrchestrator` in `thresher/controller/expansion_orchestrator.py`: log job deployment count, periodic progress (N/total complete, M failed), final summary (archives expanded, files extracted, failures, duration) per quickstart.md monitoring format
- [ ] T021 [US3] Implement expansion timeout and failure aggregation in `ExpansionOrchestrator`: raise `TimeoutError` with summary if `expansion_timeout` exceeded; populate `ExpansionResult.failed_archives`; log warnings for each failed archive; continue with successful expansions
- [ ] T022 [P] [US3] Create `tests/functional/test_expansion_e2e.py` with GCS-based tests: upload multiple small ZIPs to fake-gcs-server, run controller in local mode, verify expansion records written for all archives, verify processing queue includes both direct and expanded files

**Checkpoint**: Full pipeline works: scan → expand (parallel) → rescan → build queue → process; progress visible in logs; failures handled gracefully

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, validation, cleanup

- [ ] T023 [P] Update `docs/architecture.md` with expansion phase: add expansion orchestrator to pipeline diagram, document two-phase scan flow, describe K8s expansion job lifecycle
- [ ] T024 [P] Update `README.md` configuration section with `max_expansion_parallelism`, `upload_batch_size`, `expansion_timeout` fields and parallel expansion overview
- [ ] T025 Run quickstart.md scenarios (local mode, K8s mode, manual expansion) and verify documented commands work correctly
- [ ] T026 Run full test suite (`uv run pytest`) and verify zero regressions across all unit, functional, and integration tests

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
    │
    ├──▶ Phase 2 (Foundational) ──▶ Phase 3 (US1) ──▶ Phase 5 (US3)
    │                                                        │
    └──▶ Phase 4 (US2) ─────────────────────────────────────┘
                                                             │
                                                             ▼
                                                     Phase 6 (Polish)
```

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS US1 and US3
- **US1 (Phase 3)**: Depends on Phase 2 (needs split scanner)
- **US2 (Phase 4)**: Depends on Phase 1 only — **can run in parallel with Phase 2 and Phase 3**
- **US3 (Phase 5)**: Depends on Phase 3 (needs expansion orchestrator) and Phase 4 (needs concurrent uploads)
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Needs Foundational phase only — no dependency on other stories
- **US2 (P2)**: Needs Setup only — fully independent of other stories
- **US3 (P3)**: Needs US1 (expansion orchestrator) — integrates everything into controller flow

### Within Each User Story

- Implementation tasks before integration
- Core logic before error handling
- Test tasks [P] can run in parallel with each other

### Parallel Opportunities

**Maximum parallelism after Phase 1 completes:**
- Phase 2 (scanner refactoring) + Phase 4 (US2 concurrent uploads) run simultaneously
- Within Phase 3: T014 and T015 (test tasks) run in parallel
- Within Phase 4: T018 (tests) runs in parallel with T016/T017

---

## Parallel Example: After Setup

```
# These can all run simultaneously after Phase 1:
T006: Refactor scanner.py (Phase 2)       ─┐
T007: Update scanner tests (Phase 2)       │  Foundational track
T008: Update config.example.yaml (Phase 2) ─┘
                                            
T016: Add concurrent uploads (Phase 4/US2) ─┐
T017: Add upload retry logic (Phase 4/US2)  │  US2 track
T018: Update archive tests (Phase 4/US2)   ─┘
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (config fields, types)
2. Complete Phase 2: Foundational (scanner split)
3. Complete Phase 3: User Story 1 (parallel expansion)
4. **STOP and VALIDATE**: Test with 10 archives locally
5. This delivers the primary throughput improvement

### Incremental Delivery

1. Setup + Foundational → Infrastructure ready
2. Add US1 → Parallel expansion works (MVP!)
3. Add US2 → Upload throughput improved (can be done in parallel with US1)
4. Add US3 → Full pipeline integration, progress reporting
5. Each story adds measurable performance improvement

### Single Developer Strategy

1. Phase 1 → Phase 2 → Phase 4 (US2, simpler) → Phase 3 (US1) → Phase 5 (US3) → Phase 6
2. US2 before US1 because concurrent uploads are simpler and immediately usable by the expander CLI

---

## Notes

- All new modules follow existing `thresher/controller/` patterns
- `ExpansionOrchestrator` mirrors `K8sOrchestrator` design (config + source in constructor)
- Concurrent uploads use `concurrent.futures.ThreadPoolExecutor` (research.md R1)
- K8s completion detection: expansion records (primary) + Job status (failure fallback)
- Local mode: same `ExpansionOrchestrator` with `ThreadPoolExecutor` instead of K8s Jobs
- `scan_files()` wrapper preserved for backward compatibility
