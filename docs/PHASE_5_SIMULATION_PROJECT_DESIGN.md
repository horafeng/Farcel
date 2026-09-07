# Phase 5 — Simulation Project Architecture & Persistence Design

> Status: **Phase 5.0 design frozen; Phase 5.1 contracts completed.** This
> document specifies the boundary for later Phase 5 work. The declarative
> project DTOs and `ProjectRepository` port now exist; Phase 5.2+ JSON codec,
> save/open API, validation, `run_case`, and result persistence do not.

## 1. Goals and non-goals

Phase 5 is Farcel's backend **Simulation Project / Engineering Management**
layer. It will eventually support creating/opening/saving a local project,
managing FMU model assets, maintaining independent simulation cases, validating
a project, running a case, and retaining run history and canonical graph
results.

It is deliberately not a new numerical simulator. Phase 4 already owns graph
validation, previous-checkpoint routing, synchronous orchestration, node
runtimes, stop/progress, cleanup, and `GraphSimulationResult` semantics. Phase
5 only provides project context around those completed capabilities.

Phase 5 does not include a PySide6 graph editor, canvas, draggable blocks,
connections UI, scope widgets, window layout, direct Simulink/AMESim/ANSYS
adapters, SysML, SSP, FMI 3 Model Exchange, Scheduled Execution, strong
coupling, fixed-point/Newton iteration, database/SQLite, server storage,
distributed workers, RPC, cloud synchronization, real-time/HIL, ROM, or a
checkpoint/restart runtime.

## 2. Dependency and responsibility boundary

The frozen dependency direction remains:

```text
GUI / CLI → application → contracts ← infrastructure
```

The future project flow is:

```text
GUI / CLI
  ↓
FarcelEngine / ProjectService
  ↓
SimulationProject → SimulationCase
  ↓                    ↓
project root      resolved temporary SimulationGraph
                         ↓
              existing GraphValidator / GraphSimulationRunner
```

`Project` is above `Graph`: a graph never knows its project, repository, asset
registry, project root, or history. The existing `GraphValidator`, `DataRouter`,
`SimulationOrchestrator`, `GraphSimulationRunner`, `CoSimulationNodeRuntime`,
and `ModelExchangeNodeRuntime` retain their Phase 4 behavior unchanged.

The planned ownership is:

| Layer | Planned responsibility | Must not own |
|---|---|---|
| `contracts` | Farcel-owned project DTOs and `ProjectRepository` port | paths resolved from CWD, FMPy/native objects |
| `application` | `ProjectService`, semantic validation, safe path resolution, case orchestration | a second FMI/graph validator or scheduler |
| `infrastructure` | local JSON encode/decode, filesystem access, atomic save | FMI validation, metadata interpretation, graph execution |

FMPy objects, native FMI handles, ctypes/CVode handles, NumPy-specific runtime
types and GUI types cannot enter the project DTOs, public API, application
layer, or persisted JSON.

## 3. Frozen domain model

The later v1 domain structure is:

```text
SimulationProject
├── schema_version
├── project_id
├── name
├── model_assets: ModelAsset[]
│   ├── asset_id
│   ├── display_name
│   ├── relative_path
│   └── sha256
├── simulation_cases: SimulationCase[]
│   ├── case_id
│   ├── name
│   ├── SimulationGraph
│   └── GraphSimulationConfig
└── run_history: ProjectRunRecord[]
    ├── run_id
    ├── case_id
    ├── completion_state
    ├── final_time
    ├── completed_steps
    └── result_path
```

All IDs are opaque, stable project identifiers. Future validation rejects
duplicate `asset_id`, `case_id`, and `run_id`; it also rejects duplicate
project IDs where a repository operation has multiple applicable projects. A
case is a full independent configuration, not a patch over another case. v1
has no parent case, inheritance, override hierarchy, templates, or deltas.

`project_id`, asset IDs, case IDs, and run IDs are not model paths and have no
implicit path meaning. A run ID should be backend-generated UUID, not a global
incrementing database ID.

## 4. Directory layout and project location

The first repository format is a directory-based project:

```text
MyProject/
├── project.json
├── models/
│   ├── controller.fmu
│   ├── plant.fmu
│   └── sensor.fmu
└── results/
    └── <run_id>.json
```

The project root is storage-location context supplied to a future repository or
service call (or held in an explicit runtime-only context). It is not a
persisted `SimulationProject` field. `project.json` must never contain a
machine path such as `D:/...`, `C:\...`, `/home/...`, nor may model resolution
depend on `os.getcwd()`.

This choice makes relocation a Phase 5.3 acceptance target: copying
`D:/A/MyProject` to `E:/B/MyProject` must still permit assets and cases to load
from the new root.

## 5. Project schema and JSON principles

`project.json` has an independent top-level `schema_version`; v1 is `"1.0"`.
This version is distinct from `GraphSimulationConfig.schema_version`: one
governs the project document and the other governs the graph timing/config
contract nested in a case.

The v1 repository reads only project schema versions it explicitly supports.
An unknown or unsupported project schema is an explicit failure: no guessing,
silent downgrade, or best-effort reinterpretation is allowed. A migration
framework is intentionally not implemented in Phase 5.0.

The following shape is illustrative schema semantics, not an implementation or
serializer contract for this phase:

```json
{
  "schema_version": "1.0",
  "project_id": "project-uuid",
  "name": "MyProject",
  "model_assets": [
    {
      "asset_id": "plant-asset-uuid",
      "display_name": "Plant",
      "relative_path": "models/plant.fmu",
      "sha256": "lowercase-hex-sha256"
    }
  ],
  "simulation_cases": [
    {
      "case_id": "nominal-case-uuid",
      "name": "Nominal",
      "graph": "serialized SimulationGraph",
      "config": "serialized GraphSimulationConfig"
    }
  ],
  "run_history": []
}
```

The actual later codec must serialize the existing Farcel-owned graph DTOs;
this document does not create a second graph schema.

## 6. Model assets, paths, and metadata

`ModelAsset` v1 persists only `asset_id`, `display_name`, `relative_path`, and
`sha256`, plus explicitly approved Farcel-owned fields added by a later phase.
It must not copy FMPy `ModelDescription`, FMPy variables, native handles,
value-reference mapping objects, or NumPy values into project JSON. Current FMI
metadata still comes from `backend.load_fmu()` / the existing `ModelImporter`.

All JSON paths use canonical project-relative `/` separators. `sha256` is the
SHA-256 of the asset file bytes, encoded as lowercase hexadecimal. The project
layer must later reject empty/bad paths, absolute paths, `../` traversal,
path traversal through symlinks, and every path resolving outside the supplied
project root.

Most importantly, `asset_id` is not `ModelNode.model_path`. The existing
`ModelNode.model_path` meaning does not change. A persisted case graph contains
the canonical relative model path, for example `models/plant.fmu`, and that
same string must appear in exactly one registered `ModelAsset.relative_path`.
It must never use `ModelNode(model_path="plant-asset-uuid")`.

At validation/run time, the Project application layer performs this temporary
materialization:

```text
persisted models/plant.fmu
  → supplied project root resolution
  → D:/example/MyProject/models/plant.fmu
  → temporary resolved SimulationGraph
  → existing validate_graph() / run_graph()
```

It neither changes the persisted graph nor gives Project knowledge to
`GraphValidator`. Project validation must first check that every case node path
is registered as a `ModelAsset.relative_path`.

## 7. Validation and run-case reuse

The future `backend.validate_project(...)` operation returns project semantic
failures as `CONFIG_ERROR` with a `ValidationReport`. It owns project schema and
identity checks, asset/case/run record consistency, safe relative paths, asset
existence, SHA-256 integrity, and resolved-graph materialization.

Minimum planned validation includes project/schema identity where applicable,
duplicate asset/case/run IDs, missing asset registration, invalid or escaping
paths, missing FMUs, checksum mismatch, and a graph node model path that is not
registered by an asset. After materialization it delegates graph behavior to
the existing `GraphValidator`, which retains ownership of parameter checks,
causality, interface selection, FMI type/shape checks, connection checks, graph
timing, and runtime-capability validation. Project must not duplicate these
validators.

The later `run_case` path is exactly:

```text
resolve SimulationCase
  → resolve project-relative model paths
  → existing validate_graph
  → existing run_graph
```

It does not create a `ProjectSimulationRunner`, `ProjectScheduler`,
`ProjectDataRouter`, `ProjectOrchestrator`, `ProjectNodeRuntime`, or
`ProjectCVode`. A thin application service wrapper is acceptable only when it
does not add numerical execution semantics.

## 8. Run history, result artifacts, and restoration

Each v1 persisted run uses `results/<run_id>.json`. A `ProjectRunRecord`
records at least `run_id`, `case_id`, `completion_state`, `final_time`,
`completed_steps`, and canonical project-relative `result_path`.

`case_id` alone is insufficient provenance because the user can later edit a
case. A result artifact must therefore preserve either a canonical case snapshot
or a canonical case fingerprint, plus the SHA-256 snapshot for every asset used
by the run and the canonical `GraphSimulationResult`. This lets a later reader
explain what was executed even if the current case or asset differs.

Phase 5 "save / open / restore" means reopen the project, restore ModelAssets,
Cases and RunHistory, and reread historical `GraphSimulationResult` artifacts.
It does not save a CVode integrator state, an FMI native instance, or simulation
time for continuation after the process closes. Checkpoint/restart and
distributed recovery are outside Phase 5.

The future result JSON codec must round-trip canonical timestamps,
completion state, nested `node_id → variable_name → samples`, nested arrays,
Boolean, String, Enumeration and numeric values. It must not flatten output
names into `node.variable` strings. Before implementation it must specify an
explicit standard-JSON representation for NaN and positive/negative infinity;
relying on an undocumented non-standard JSON encoder behavior is forbidden.

## 9. Persistence and error rules

The later local JSON repository owns only encode/decode and filesystem
persistence. Saving `project.json` must write a sibling temporary file, flush
and close it, then atomically replace `project.json`. It must not truncate the
existing file before a new valid document is safely ready; a save failure must
leave the last usable project document intact.

Project semantic validation uses `CONFIG_ERROR` plus `ValidationReport`.
Later persistence work should add stable, additive `PROJECT_FORMAT_ERROR` and
`PROJECT_IO_ERROR` codes for malformed/unsupported documents and filesystem
failures. `EXPORT_ERROR` must not be repurposed for project load/save. Phase
5.0 does not modify `ErrorCode`.

## 10. Frontend boundary and staged implementation

The backend will expose only project domain/persistence/orchestration behavior.
PySide6 owns the graph editor, canvas, blocks, visual connections, scopes and
project UI. GUI consumers continue to use `from farcel import create_backend`
and `from farcel.contracts import ...`; they do not import FMPy, infrastructure,
or CLI internals.

The intended delivery sequence is:

| Stage | Planned scope | Explicitly not delivered by Phase 5.0 |
|---|---|---|
| 5.1 | **Completed**: Farcel project DTO/port and contract tests | persistence/runtime |
| 5.2 | local JSON repository, schema and atomic save/open | run-case execution |
| 5.3 | project validation, path resolution, relocation acceptance | altered Graph semantics |
| 5.4 | case orchestration, run history/result codec and provenance | checkpoint/restart or distributed runtime |
| Frontend work | PySide6 project/graph UI | backend numerical implementation |

Each later stage must remain additive, preserve existing public Graph APIs and
tests, and separately validate supported project schemas. This Phase 5.0 design
freeze implements none of those stages.
