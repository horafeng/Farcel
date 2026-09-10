# Phase 6 — Scenario Batch & Result Post-processing Design

## Status

**Phase 6 backend milestone — Completed.** Phase 6.0 design freeze completed, Phase 6.1–6.3 implementation completed, and Phase 6.4 public example/documentation/CI finalization completed. This document remains the frozen Phase 6.0 design record; its original semantic commitments below are retained.

## Implementation status / delivered API

| Stage | Status | Delivered public surface |
|---|---|---|
| 6.1 | Completed | `export_graph_result(result, destination)` for canonical and loaded historical graph results |
| 6.2 | Completed | `run_project_cases(project_root, project, case_ids, ...)` local synchronous serial batch |
| 6.3 | Completed | `compare_project_runs(artifacts)` immutable artifact-only comparison and scalar statistics |
| 6.4 | Completed | Public Phase 6 workflow example, documentation synchronization, and CI coverage |

## GitHub baseline

| Item | Value |
|---|---|
| Repository | https://github.com/horafeng/Farcel |
| Required main HEAD | c5d0ba4af44f7929289a6abf6d9709c7dde63f6f |
| Merge parents | 7d6188fb2561c64cfa0f1b66702a346982540505; 4a1315956c4eba11daa2e5c087f2da010366a258 |
| Design branch | phase-6-work, created directly from the required HEAD |

Phase 6 work starts only with a clean working tree and origin/main at the required SHA. A changed origin/main is a stop condition: do not merge, rebase, reset, absorb frontend work, or continue implementation.

## Rationale

Phase 5 already delivered multiple SimulationCase entries, one-case public execution, immutable result artifacts, and historical provenance. The immediate product gap is not a new FMU runtime: callers need to batch selected existing cases, export canonical graph results, and compare immutable historical runs.

The former Phase 6 direct Simulink / AMESim / ANSYS adapter plan is rescaled, not cancelled. Proprietary direct-tool adapters remain Later / Long-term direct-tool integration candidates after explicit feasibility, verification, and maintenance plans; they are not the milestone immediately following Phase 5.

## Frontend integration gap audit

This is a read-only audit of origin/feat/frontend. No frontend commit is merged or copied into this branch.

| Audit item | Finding |
|---|---|
| Branch position | HEAD 43518ceec83236b27cd9413e545f1973fab014f0; one Phase 2 GUI commit over merge-base 10947e9894debc77e3412709c6e401841893c34b |
| Integrated backend phase | Phase 2 single-FMU public workflow, not Phase 4/5 APIs now on main |
| Graph API | No SimulationGraph, validate_graph(), or run_graph() use |
| Project API | No SimulationProject, run_project_case(), or load_project_run() use |
| Run history / multi-case UI | No RunHistory UI, case selection, or batch UI |
| Graph result export | No graph exporter; UI calls export_result() only for a current single SimulationResult |
| Presentation helpers | gui.presenter computes single-result statistics, plot series, and array flattening; gui.plot_dialog renders with matplotlib |

The presentation helpers are correctly frontend work. Phase 6 must define canonical signal identity, export schema, comparison semantics, and mathematical statistics in the backend. It must not absorb PySide6, matplotlib layout, color/style selection, table or number formatting, labels, interaction, or UI-only array display helpers.

## Goals

The final product flow is:

    SimulationProject
      -> caller-selected SimulationCase
      -> existing run_project_case()
      -> ProjectRunArtifact / ProjectRunRecord / RunHistory
      -> export, batch, comparison and basic post-processing
      -> frontend visualization

Users will select multiple cases, run them in order, retain independent persisted history for each run, reopen history, export graph results, and compare historical signals. Phase 6 does not add a ProjectRunner, a second project runtime, or a new numerical scheduler.

## Non-goals and invariant architecture

Phase 6 preserves GUI / CLI -> application -> contracts <- infrastructure, create_backend(), FMI 2/3 Co-Simulation, FMI2 Model Exchange CVode/BDF, explicit Jacobi previous-checkpoint ZOH, GraphValidator, DataRouter, SimulationOrchestrator, GraphSimulationRunner, node runtimes, ProjectValidator, relative project paths, asset SHA-256, provenance, and artifact-first / project-second persistence.

It does not deliver FMI3 Model Exchange, Scheduled Execution, SSP, direct adapters, parallel/distributed execution, workers, RPC, cloud, a database, checkpoint/restart, real-time/HIL, a native/C++ rewrite, a new CVode solver, a graph scheduler, strong coupling, fixed-point/Newton loops, GraphResultChunk, graph streaming, interpolation/resampling, or advanced DSP/statistics.

No Phase 6 API may create a second graph validator, project solver, project scheduler, project runtime, or competing result truth model.

## Architecture boundary

Batching is application orchestration over the stable one-case entry point. A future batch implementation must call run_project_case() once per requested case; it must not invoke a lower graph runtime, construct sessions, or bypass project validation and persistence. A project result remains the GraphSimulationResult held by ProjectRunArtifact.

Historical operations start with:

    load_project_run(project_root, project, run_id)
      -> ProjectRunArtifact
      -> artifact.case_snapshot / artifact.asset_snapshots / artifact.result

They do not revalidate current cases, inspect current FMU checksums, re-run models, or derive truth from mutable project state.

## Phase 6.1 design — Graph / historical result export

The only export input is canonical GraphSimulationResult. Historical export first loads its artifact and passes artifact.result to the same exporter. It must not execute an FMU, reconstruct a timeline, read FMPy, parse project.json, or parse a results JSON file into another flattening path.

At Phase 6.0 design time, the exporter facade was intentionally deferred. The
completed delivered API is `export_graph_result(result, destination)`, reusing
`ExportReport` where sufficient and the existing infrastructure/export error
style.

The CSV semantics are frozen:

- Header column zero is exactly time; rows follow result.timestamps in canonical order, including valid STOPPED samples.
- A scalar signal header is node/<node-id>/<variable>. Both path segments use JSON-Pointer escaping: ~ becomes ~0 and / becomes ~1.
- Columns sort node IDs, then variable names, in Unicode code-point order; mapping insertion order cannot change the file.
- Arrays expand in row-major, zero-based index order, adding [i,j,...] to the scalar header. Shape uses existing Farcel array rules, never frontend display flattening.
- STOPPED export is supported with row_count equal to result.sample_count and no invented samples after final_time.
- Destination behavior remains UTF-8 CSV, create parent directories, overwrite the exact destination, and do not append an extension. Failures use stable EXPORT_ERROR with destination and diagnostic details.

## Phase 6.2 design — Serial project batch execution

The first batch is local, synchronous, serial, and accepts caller-selected case IDs. Request order is execution and result order. Each item invokes existing run_project_case(), retaining its own run_id, ProjectRunArtifact, ProjectRunRecord, and results/<run_id>.json file.

The batch is not a transaction. A completed or stopped item remains committed when a later item fails. Every item keeps artifact-first, project-second persistence; there is no batch.json and neither schema 1.0 changes.

If batch result/progress contracts are necessary, they will be Farcel-owned and implementation-independent. The API name is not chosen in 6.0. These semantics are frozen:

| Request/event | Behavior |
|---|---|
| Duplicate requested case_id | Reject before the first run as CONFIG_ERROR; do not silently deduplicate or run twice. |
| Unknown case_id | Reject before the first run as CONFIG_ERROR; no partial batch starts. |
| Empty request | Reject as CONFIG_ERROR; no persistence occurs. |
| Runtime/persistence failure after earlier items | Stop the batch, retain earlier commits, surface the stable item error with completed-item context. |
| Stop before first item | Existing CANCELLED; no artifact or record. |
| Stop during an item | Let run_project_case() return and persist STOPPED; do not begin another item. |
| Progress | Batch item identity/count plus forwarded item progress; it does not redefine graph progress. |

## Phase 6.3 design — Historical comparison and basic post-processing

Comparison sources are immutable ProjectRunArtifact values. A signal identity is structural (run_id, node_id, variable_name), never a parsed display string. Every source keeps its native timestamps: the backend provides no interpolation, resampling, or common time grid. The frontend may overlay different axes.

First-version statistics apply only to numeric scalar samples: min, max, arithmetic mean, and final. Booleans, strings, arrays, missing nodes/signals, and empty numeric data are marked unavailable or non-applicable in a future Farcel-owned DTO; they are never coerced into numeric results. Array expansion is an export rule, not automatic first-version comparison expansion.

STOPPED artifacts compare only their recorded native samples. Results must retain display metadata identifying immutable run provenance, case snapshot, asset snapshots, completion state, and final time. Different case snapshots and timestamps are information to present, never a reason to re-run or normalize. RMS, integrals, FFT, error norms, confidence bands, signal-processing frameworks, interpolation, and resampling remain out of scope.

## Persistence, schema, error, and stop semantics

The Phase 6 design preserves:

    PROJECT_SCHEMA_VERSION == "1.0"
    PROJECT_RUN_ARTIFACT_SCHEMA_VERSION == "1.0"

Export is derived output, batch reuses one-case persistence, and comparison reads immutable artifacts. A later schema change requires concrete unavoidable contract evidence; Phase 6.0 changes neither schema.

Public failures remain EngineError with stable ErrorCode and actionable details. Export uses EXPORT_ERROR, request validation uses CONFIG_ERROR.details["issues"], and existing project artifact/path/codec failures retain their project codes. Native, FMPy, GUI, and CSV-library exceptions cannot leak through public contracts.

Failures never fabricate results. A pre-start cancel has no artifact; a STOPPED graph result follows normal one-case persistence and becomes historical truth. Artifact save failure leaves project.json unchanged. If artifact save succeeds but project save fails, the existing immutable orphan and diagnostic details remain; there is no rollback or transaction.

## Frontend / backend ownership

| Backend owns | Frontend owns |
|---|---|
| Historical provenance; batch/persistence semantics; graph CSV schema; signal identity; comparison rules; mathematical statistic definitions; stable errors | PySide6; worker/thread integration; matplotlib; plot layout; curve color/style; table and number formatting; labels; interaction; MATLAB-Simulate-like presentation |

The frontend consumes public APIs and marshals synchronous calls/callbacks to its UI thread. It may format raw values and make presentation-only plot traces, but may not redefine persisted truth or backend comparison/statistics semantics.

## Phase 6.0–6.4 split

| Stage | Scope | Not delivered |
|---|---|---|
| 6.0 | Design freeze and frontend integration gap audit | Runtime/API implementation; frontend changes |
| 6.1 | Graph and historical-result CSV export | Batch, comparison, GUI |
| 6.2 | Serial project batch over run_project_case() | Parallel/distributed scheduling, schema transaction |
| 6.3 | Historical comparison and basic scalar post-processing | Interpolation, resampling, advanced analytics |
| 6.4 | Public API, examples, documentation, CI finalization | Unrelated runtime or frontend expansion |

## Acceptance criteria

1. APIs are additive, Farcel-owned, and preserve the architectural boundary without FMPy or GUI type leaks.
2. Canonical and loaded historical graph results export identically, with deterministic columns, arrays, and STOPPED samples.
3. Batch preserves request order, delegates every item to run_project_case(), rejects invalid requests before execution, and retains earlier commits after failure.
4. Comparison reads immutable artifacts, preserves native axes, identifies signals structurally, and calculates only applicable simple scalar statistics.
5. Project and artifact schema versions remain 1.0 unless separately evidenced.
6. Tests, public examples, documentation, CI, and consumer boundaries pass without Graph numerical changes or frontend merge.

The original Phase 6.0 design freeze contained no Phase 6.1 implementation;
the completed later-stage delivery is recorded in the status table above.
