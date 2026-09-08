# Phase 5 — Simulation Project Architecture & Persistence Design

> Status: **Phase 5.0 design frozen; Phase 5.1 contracts completed; Phase 5.2
> local JSON repository completed; Phase 5.3A asset integrity completed; Phase
> 5.3B project validation and graph materialization completed; Phase 5.4A
> ProjectService / `run_case` orchestration, Phase 5.4B result-artifact
> codec/provenance, Phase 5.4C1 result-artifact filesystem repository, and
> Phase 5.4C2 run-history/project persistence integration completed; Phase
> 5.5A public project lifecycle API, Phase 5.5B public historical run loading
> API, Phase 5.5C public persistent project-case execution API, and Phase 5.6
> public example/documentation/CI finalization completed. Phase 5 backend scope
> is complete.**

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

Phase 5.2 serializes these existing Farcel-owned graph DTOs directly; it does
not create a second graph schema. Its canonical UTF-8 output uses the field
order shown above, `ensure_ascii=False`, two-space indentation and a trailing
newline. Only project schema `"1.0"` is accepted for save/load.

## 6. Model assets, paths, and metadata

`ModelAsset` v1 persists only `asset_id`, `display_name`, `relative_path`, and
`sha256`, plus explicitly approved Farcel-owned fields added by a later phase.
It must not copy FMPy `ModelDescription`, FMPy variables, native handles,
value-reference mapping objects, or NumPy values into project JSON. Current FMI
metadata still comes from `backend.load_fmu()` / the existing `ModelImporter`.

All JSON paths use canonical project-relative `/` separators. `sha256` is the
SHA-256 of the asset file bytes, encoded as lowercase hexadecimal. Phase 5.3A
now checks one `ModelAsset` against an explicitly supplied project root:
canonical relative syntax (including cross-platform absolute-path rejection),
resolved-root containment including symlink escape, existing regular file,
64-character lowercase SHA-256 syntax, and streamed file-content checksum.
These checks enable relocation after a whole project directory is copied. They
do not validate project-wide duplicates, case bindings, or graph semantics.

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
`GraphValidator`. Phase 5.3B checks project/case/run identifiers and duplicate
asset paths, aggregates single-asset integrity reports, binds each case node by
exact `ModelAsset.relative_path`, and builds this temporary absolute-path graph
for existing `GraphValidator` validation. Project-wide validation does not add
another FMI, parameter, connection, or timing validator.

Phase 5.4A adds only a thin `ProjectService`: it selects one case by exact ID,
validates the project, rechecks the target case's assets, materializes a
temporary absolute-path graph, and delegates unchanged to existing `run_graph`.
It forwards `RunControl` and graph progress unchanged and returns the existing
`GraphSimulationResult` unchanged. It does not add result persistence,
provenance artifacts, run IDs, run-history mutation, public backend project
APIs, asset import/copy operations, or repository semantic validation.

## 7. Validation and run-case reuse

Phase 5.5A exposes `open_project(...)`, `save_project(...)`, and
`validate_project(...)` on the public backend created by `create_backend()`.
Open and save are structural repository operations: they do not automatically
perform semantic project validation. Validation returns a valid
`ValidationReport` or reports project semantic failures as `CONFIG_ERROR` with
the stable issue details format. It owns project schema and
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

Phase 5.4B freezes the independent `ProjectRunArtifact` schema version at
`"1.0"`. Its in-memory artifact contains `run_id`, `project_id`, a complete
persisted-relative `SimulationCase` snapshot, first-use-ordered snapshots of
only the case's used assets (`asset_id`, `relative_path`, `sha256`), and the
canonical `GraphSimulationResult`. The provenance builder does not read files
or rehash assets: it snapshots the checksum that 5.4A already rechecked.

The corresponding JSON codec is string-only in 5.4B. It uses strict top-level
fields, UTF-8 JSON style (`ensure_ascii=False`, two-space indentation, trailing
newline), and `allow_nan=False`. Finite floats remain JSON numbers; result
samples use exactly `{"$farcel_float":"nan"}`,
`{"$farcel_float":"positive_infinity"}`, or
`{"$farcel_float":"negative_infinity"}` for non-finite floats. Raw JSON
`NaN`/`Infinity` constants are rejected. Nested result outputs remain
`node_id → variable_name → samples`, and decoded arrays are tuples.

Each future persisted run will use `results/<run_id>.json`. A `ProjectRunRecord`
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

Phase 5.4C1 now persists only the independent artifact using
`results/<run_id>.json`. It accepts portable safe run IDs (letters/digits first,
then letters/digits/`-`/`_`/`.` only), returns the canonical relative path, and
accepts only that exact `results/<safe-run-id>.json` shape on load. It writes
the 5.4B codec text as UTF-8 through a sibling temporary file, flushes and
fsyncs it, then commits atomically. Existing artifact files are immutable: a
duplicate run ID fails rather than overwriting history. Real-path containment
rejects traversal and symlink escape; relocation works because no absolute
project path is stored. Format/path/codec failures use `PROJECT_FORMAT_ERROR`;
filesystem, missing-file, and existing-target failures use `PROJECT_IO_ERROR`.
Windows reserved device stems (including extension forms such as `CON.backup`)
are rejected in addition to the portable filename character policy.

Phase 5.4C2 now connects only completed canonical results to persistence. Its
application service generates a UUID-hex run ID, rejects a duplicate current
history ID, builds the 5.4B artifact, saves the C1 artifact first, then creates
and appends `ProjectRunRecord` in a new immutable `SimulationProject` before
saving `project.json`. `COMPLETED` and `STOPPED` results both follow this path.
The caller's project object is unchanged and existing history order is kept.

Phase 5.5B exposes public historical run loading without adding execution:

```python
backend.load_project_run(project_root, project, run_id) -> ProjectRunArtifact
```

The facade follows `run_id → ProjectRunRecord → result_path →
ProjectRunArtifactRepository → ProjectRunArtifact`. It uses only the exact
history record and immutable artifact: it does not revalidate current FMUs or
assets, compare the current case with `artifact.case_snapshot`, mutate the
project, or execute a simulation. This permits historical results to remain
readable after a current asset is removed or a case is edited. Phase 5.5C now
provides the public persistent project-case execution facade:

```python
updated_project, run_record, result = backend.run_project_case(
    project_root,
    project,
    case_id,
    control=None,
    on_progress=None,
)
```

It executes the caller-supplied project snapshot through the existing
`ProjectService.run_case`, then persists the resulting canonical graph result
through the existing `ProjectRunPersistenceService.persist_run`. The input
project remains unchanged; callers use `updated_project` as their next current
project state. A successful return means the artifact and updated `project.json`
are committed, so `load_project_run` can immediately restore the history item.
`STOPPED` results follow the same persistence path.

The public lifecycle is therefore:

```python
backend = create_backend()
project = backend.open_project(root)
backend.validate_project(root, project)
project, record, result = backend.run_project_case(root, project, "case-1")
historical = backend.load_project_run(root, project, record.run_id)
```

Phase 5.5C adds no project scheduler or numerical runner, checkpoint/restart,
database transaction, distributed runtime, CLI project command, GUI, public
example, case CRUD helper, or asset import/copy workflow.

The frozen cross-file failure policy is deliberately not a transaction: if the
artifact save fails, `project.json` is untouched. If artifact save succeeds but
the project save fails, the artifact remains as an immutable orphan and the old
`project.json` remains without a dangling history entry. The re-raised project
error retains its code/details and adds `run_id`, `orphan_result_path`, and
`history_committed=False`. No rollback delete, database transaction, or journal
is introduced.

## 9. Persistence and error rules

The Phase 5.2 local JSON repository owns only encode/decode and filesystem
persistence. Saving `project.json` encodes the complete document before I/O,
writes a sibling temporary file in UTF-8, flushes and fsyncs it, closes it, and
then uses atomic replacement. It never truncates an existing `project.json`
before a new valid document is ready; I/O failure leaves the prior document
intact and best-effort removes the temporary file.

Project semantic validation is available through the Phase 5.5A public facade
as `CONFIG_ERROR` plus `ValidationReport` concern. Phase 5.2 adds stable, additive
`PROJECT_FORMAT_ERROR` and `PROJECT_IO_ERROR` codes for malformed/unsupported
documents and filesystem failures. `EXPORT_ERROR` is not repurposed for project
load/save.

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
| 5.2 | **Completed**: local JSON repository, schema and atomic save/open | run-case execution |
| 5.3A | **Completed**: asset path/integrity/relocation foundation | project-wide validation or altered Graph semantics |
| 5.3B | **Completed**: project validation and graph materialization | Graph execution or case orchestration |
| 5.4A | **Completed**: ProjectService, one-case `run_case`, validation, asset recheck and `run_graph` delegation | result persistence or public backend Project API |
| 5.4B | **Completed**: result artifact DTO, provenance snapshot and strict JSON codec | filesystem persistence or run-history mutation |
| 5.4C1 | **Completed**: safe atomic `results/<run_id>.json` artifact repository | run-history or project persistence integration |
| 5.4C2 | **Completed**: artifact-first run history and project persistence integration | public backend Project API or checkpoint/restart |
| 5.5A | **Completed**: public project open/save/validate lifecycle API | historical result loading or persistent project-case execution |
| 5.5B | **Completed**: public historical run loading | persistent project-case execution |
| 5.5C | **Completed**: public persistent project-case execution | public example and final Phase 5 hardening |
| 5.6 | **Completed**: public project example, frontend integration documentation, CI example coverage and final Phase 5 acceptance preparation | later product work |
| Frontend work | PySide6 project/graph UI | backend numerical implementation |

Each later stage must remain additive, preserve existing public Graph APIs and
tests, and separately validate supported project schemas. Phase 5 backend scope
is complete: it delivers project DTOs, JSON save/open, asset safety/integrity
and relocation, project validation, case execution through the existing graph
runtime, immutable result artifacts, run history, artifact-first persistence,
and public lifecycle/history/execution APIs. It does not deliver checkpoint /
restart, database or distributed execution, SSP, direct Simulink/AMESim/ANSYS
adapters, realtime/HIL, FMI3 Model Exchange runtime, Scheduled Execution
runtime, graph CSV export, or a frontend GUI implementation.
