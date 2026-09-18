# Phase 7.5B Distributed Execution Integration Design Review

## Status and scope

> **Historical planning/design-review snapshot.** This review froze against
> commit `a4432cedb54e0290412f49335ae392a4beab88b6` on `phase-7-work`. Phase 7
> was subsequently completed and merged into `main`: localhost distributed
> graph execution, including `ExecutionPlan`, mixed LOCAL/WORKER and
> two-Worker graphs, is a delivered capability. The roadmap below is not a
> current task list. In particular, its project-placement/schema 1.1 proposal
> was not delivered: `PROJECT_SCHEMA_VERSION` remains `"1.0"`,
> `run_project_case` remains local-only, and runtime distribution uses the
> direct public graph API with an `ExecutionPlan`.

This review freezes the design against commit `a4432cedb54e0290412f49335ae392a4beab88b6` on `phase-7-work`. It is an audit and roadmap snapshot, not an implementation change.

The codebase is more complete than the Phase 7.5A label alone suggests: the default public composition root already installs `TcpDistributedGraphExecutor`, and public localhost end-to-end tests exercise both mixed and two-worker graphs.  Phase 7.5B must therefore **stabilize and integrate** that implementation; it must not reimplement the Worker protocol, TCP transport, graph scheduler, or a second distributed executor.

The supported deployment boundary remains a pre-existing, localhost Worker endpoint.  `create_backend()` does not start a process and does no network I/O.  `LocalWorkerSubprocess` is a coordinator-owned bootstrap adapter used by tests/development; it is not invoked by the public backend path.

## 1. Current architecture

### Dependency and control-flow model

```text
public caller
  -> create_backend() / FarcelEngine.run_graph(graph, config, execution_plan=...)
  -> TcpDistributedGraphExecutor (only when at least one node is WORKER)
  -> GraphRuntimeBindingsFactory.create_with_plan(...)
  -> GraphSimulationRunner
  -> SimulationOrchestrator (Coordinator owns time, routing, barriers, commit)
  -> RemoteNodeRuntime for each WORKER node
  -> WorkerRpcClient -> TcpWorkerClient -> Worker subprocess / Worker service
```

For a LOCAL node, `GraphRuntimeBindingsFactory` instead uses the existing importer and local Co-Simulation or Model-Exchange runtime factory.  Every graph still has one Coordinator-owned `DataRouter`, one previous immutable snapshot, an all-input-set barrier, an advance-all barrier, and an all-output-read barrier.  Workers never connect to each other and never receive graph topology, routing, run control, or global checkpoint ownership.

### Layer-by-layer audit

| Layer | Location | Input -> output | Responsibility | Assessment |
| --- | --- | --- | --- | --- |
| Public facade | `src/farcel/application/engine.py` (`FarcelEngine.run_graph`) | `SimulationGraph`, `GraphSimulationConfig`, optional `ExecutionPlan` -> `GraphSimulationResult` | Stop precheck; graph and plan validation; chooses local or distributed path; converts unexpected executor failures to `EngineError`. | Stable public additive API. |
| Distributed executor contract | `src/farcel/application/distributed_execution.py` | Same graph/config/plan inputs -> result | `DistributedGraphExecutor` Protocol describes the injection seam. | Intentional application seam, not the default implementation. |
| Default composition and implementation | `src/farcel/backend.py`, `src/farcel/distributed_backend.py` | A validated plan -> connected `WorkerRpcClient`s, remote provisioners, result | `create_backend()` builds `TcpDistributedGraphExecutor`; executor selects only used workers, connects, PINGs, owns TCP client cleanup, and invokes the normal graph runner. | Implemented for pre-existing TCP Workers. It does **not** create or restart Workers. |
| Runtime binding | `src/farcel/application/graph_runtime_factory.py` | graph/config/plan plus worker-ID-to-provisioner map -> ordered `(node_id, ModelNodeRuntime)` tuple | Validates plan before any runtime creation; creates local or remote runtimes while preserving declaration order; closes partially created bindings on failure. | Stable composition boundary. `RemoteRuntimeProvisioner` is an intentional seam. |
| Whole-run lifecycle | `src/farcel/application/graph_runner.py` | graph/config and bindings -> canonical graph result | Builds orchestrator, samples results/progress, terminates then closes every runtime, aggregates cleanup failures. | Stable, placement-agnostic. |
| Checkpoint scheduler | `src/farcel/application/simulation_orchestrator.py` | node runtime bindings, graph config, route callback -> immutable snapshots | Owns explicit-Jacobi routing, all-set/all-read barriers, checkpoint commit and failure state. | Stable; must remain Worker-agnostic. `NodeAdvanceExecutor` is an injectable execution-policy seam. |
| Remote runtime proxy | `src/farcel/application/remote_node_runtime.py` | `ModelNodeRuntime` calls -> protocol payloads / output mapping | Enforces remote lifecycle and turns Worker commands into the same local node-runtime surface. | Implemented and stable for a connected client. |
| RPC and provisioning | `src/farcel/application/worker_client.py`, `remote_runtime_factory.py`, `worker_asset_staging.py` | typed Worker DTOs and local model path -> typed payloads / `RemoteNodeRuntime` | Generates request IDs, maps remote errors, stages content-addressed FMU bytes, creates remote runtime IDs. | Implemented; no retry, replay, reconnect, or connection ownership. |
| Transport and Worker | `src/farcel/infrastructure/worker_protocol/`, `worker_process.py`, `src/farcel/worker.py` | framed TCP request/response -> Worker service operation | Codec/framing/TCP transport; optional localhost child bootstrap; Worker service owns worker-local cache and native runtime registry. | Protocol/transport/subprocess are implemented. Public distributed execution consumes an endpoint, not a launcher. |

### Stable contracts versus seams

Stable public contracts are `ExecutionPlan`, `WorkerDescriptor`, `WorkerEndpoint`, `NodePlacement`, `PlacementKind`, `FarcelEngine.validate_execution_plan`, `FarcelEngine.run_graph(..., execution_plan=...)`, and the matching `SimulationEngine` protocol methods.  They are exported from `farcel.contracts`; the root `farcel` package deliberately stays small and does not re-export the distributed DTOs.

The seams are deliberate: `DistributedGraphExecutor` permits application-level substitution; `RemoteRuntimeProvisioner` permits binding tests and alternate compositions; `NodeAdvanceExecutor` permits an advance policy without changing scheduling semantics; and `WorkerTransportClient` keeps `WorkerRpcClient` transport-independent.  None of these seams authorizes a second scheduler, Worker-local routing, or direct Worker-to-Worker communication.

## 2. Phase 7.5A completion audit

### A. ExecutionPlan public API

**Complete.** A public consumer can import the DTOs from `farcel.contracts`, pass an `ExecutionPlan` to `create_backend().run_graph`, and call `validate_execution_plan` independently. `ExecutionPlanValidator` is pure: it does not load an FMU, create a runtime, connect to a Worker, or mutate graph/plan state. Invalid plans return the established issue schema through `CONFIG_ERROR` before the executor or any native runtime is used.

No plan, an empty plan, explicit LOCAL placements, and unused Worker descriptors all retain the existing local `GraphSimulationRunner` path. A used WORKER placement never silently falls back to LOCAL.

### B. DistributedGraphExecutor

**Contract and default implementation are complete.** `DistributedGraphExecutor` is only a Protocol, but `TcpDistributedGraphExecutor` is the concrete default wired by `create_backend()`. It owns exactly the TCP client sessions it creates for the run; it PINGs each used Worker, builds one `RemoteNodeRuntimeFactory` per used Worker, runs the normal graph lifecycle, then closes client sessions in reverse order. It cleans partial connections and aggregates cleanup failures without replacing an existing `EngineError`.

**Worker creation is intentionally not part of this executor.** The descriptor endpoint must already identify a reachable Worker. `LocalWorkerSubprocess` remains an external/bootstrap concern.

### C. Engine integration

**Complete for public localhost TCP execution.** `run_graph` adds only the keyword-only `execution_plan` parameter. Existing graph callers and `run_fmu` signatures remain unchanged. The local path is unchanged and public integration tests prove mixed LOCAL-to-WORKER and two-WORKER execution through `create_backend()`.

### D. Backend contract

**Complete.** `SimulationEngine` contains both `validate_execution_plan(graph, plan)` and the additive `run_graph(..., execution_plan=None)` signature. The default backend composition installs the concrete executor without opening a connection until a WORKER placement is actually used.

## 3. Historical gap list and scope decisions

### Historical work proposed before declaring Phase 7.5B integrated

1. **Freeze the executor lifecycle contract in focused tests.** Add direct tests for used-worker selection, PING/connect failure, partial multi-worker connection cleanup, reverse close order, cleanup-failure aggregation, and the invariant that the primary run failure remains primary. The behavior exists, but it currently relies heavily on end-to-end coverage.
2. **Document the public operating contract.** State that endpoint Workers must already be running, that the supported transport is localhost TCP, that no automatic spawn/restart/retry/replay occurs, and that a Worker crash prevents checkpoint commit rather than causing recovery.
3. **Keep ownership boundaries explicit.** A graph runner owns node terminate/close; the distributed executor owns only the TCP clients it opened; a local launcher owns only its child process. No Phase 7.5B change may blur these responsibilities.

### Historical follow-on proposals after lifecycle stabilization

1. **Project-aware distributed execution design and implementation.** Today `SimulationCase` contains only graph and graph config; `ProjectService.run_case` calls `run_graph` without an execution plan. A project case therefore cannot replay a distributed placement even though the public graph API can.
2. **A public example and operational documentation.** Provide one portable local graph example plus one explicitly local-machine, pre-started Worker example. Examples must use only `create_backend()` and `farcel.contracts`.
3. **Focused observability at the API boundary.** Preserve existing `EngineError` details (`worker_id`, `node_id`, `phase`, and `cleanup_failures`) in documented examples and contract tests rather than introducing a new logging/scheduler subsystem.

### Do not do in Phase 7.5B

- GUI workflow or a visual topology/placement editor.
- Scheduler, Worker pool, dynamic discovery, elastic cluster management, Kubernetes, or cloud deployment.
- LAN bind, TLS, authentication/authorization, multi-tenant isolation, or remote artifact registry.
- Automatic Worker start/restart, reconnect, retry, replay, checkpoint recovery, or distributed rollback.
- A production concurrent/parallel advance pool. The existing executor seam remains sufficient for deterministic tests; the Coordinator's barrier semantics remain unchanged.

## 4. Project-system decision

### Current state

`PROJECT_SCHEMA_VERSION` is `1.0`. `SimulationCase` has `case_id`, `name`, `graph`, and `config`; the strict JSON repository accepts exactly those case fields. Neither `project.json` nor `run_project_case` accepts an `ExecutionPlan`.

### Decision

**Do not add the current endpoint-bearing `ExecutionPlan` directly to `SimulationCase` in Phase 7.5B.1.** A `WorkerDescriptor` contains deployment-specific host and port data. Persisting that object inside a portable project would couple a project file to a particular machine and later make TLS/auth/credential policy difficult to evolve.

**Project placement is needed, but only as a later, separated contract.** In Phase 7.5B.3, introduce an optional project-owned logical placement profile (node ID -> logical worker reference) and a runtime-supplied Worker registry (logical worker reference -> `WorkerEndpoint`). Compile the pair into the existing `ExecutionPlan` at the engine boundary. This keeps host/port and future credentials out of `project.json`, while allowing an explicit caller-supplied all-LOCAL registry/profile for portable default behavior.

If that later scope is approved, the schema becomes `1.1`:

- A 1.0 document loads as a 1.1 in-memory project with no placement profile, meaning LOCAL-only behavior.
- A 1.1 document serializes the optional logical placement profile only; it never serializes credentials or process ownership.
- The repository must accept both versions, decode 1.0 without data loss, and write a canonical 1.1 document only after an explicit save.
- `run_project_case` gains an additive keyword-only deployment/registry argument. Its omission preserves the 1.0 local behavior.

This is a forward design decision, not a schema change made by this review.

## 5. Historical Phase 7.5B roadmap (not current work)

### Phase 7.5B.1 — Executor lifecycle contract hardening

- **Goal:** Freeze the already implemented `TcpDistributedGraphExecutor` ownership and failure semantics with focused tests; make no public API expansion.
- **Modify:** `tests/unit/test_distributed_backend.py` (new), potentially `tests/integration/test_public_distributed_graph_api.py` for one missing public assertion, and only `src/farcel/distributed_backend.py` if a test exposes a genuine contract defect.
- **Do not modify:** `SimulationOrchestrator`, `GraphSimulationRunner`, Worker protocol DTOs/framing, Worker service, `FarcelEngine` public signatures, project schema, GUI/CLI.
- **Tests:** Used-worker filtering; zero network activity for LOCAL-only plans; connect/PING failure; partial two-worker connect cleanup; reverse client close; primary failure plus cleanup failure; no retry/reconnect/restart.
- **Acceptance:** All existing distributed parity/crash tests remain green, new tests prove lifecycle ownership directly, and local graph behavior remains byte-for-byte API-compatible.

### Phase 7.5B.2 — Public operating contract and examples

- **Goal:** Make the currently supported execution model discoverable and unambiguous to a public API consumer.
- **Modify:** `docs/PHASE_7_DISTRIBUTED_EXECUTION_DESIGN.md`, `docs/FRONTEND_BACKEND_INTEGRATION.md` as applicable, a new concise distributed public API example, and integration tests that execute the example or its equivalent.
- **Do not modify:** Production scheduler/routing code, Worker protocol, schema, cloud/cluster components.
- **Tests:** Example imports only `farcel` and `farcel.contracts`; it uses a pre-started localhost Worker descriptor; LOCAL-only and placed-worker results retain parity; documented error details are stable.
- **Acceptance:** Documentation clearly says who starts Workers, who owns TCP connections, what is unsupported, and how a crash maps to no checkpoint result.

### Phase 7.5B.3 — Project placement-profile contract (decision gate, then narrow implementation)

- **Goal:** Add portable, explicit project-level placement without persisting host/port or credentials.
- **Modify after approval:** `src/farcel/contracts/project.py`, a new project placement contract module, `src/farcel/infrastructure/project/json_repository.py`, project validation/service/engine call path, `src/farcel/contracts/ports.py`, and targeted project contract/repository/public integration tests.
- **Do not modify:** Existing `ExecutionPlan` semantics, Worker protocol/TCP transport, graph scheduler, Worker subprocess ownership, GUI.
- **Tests:** 1.0 load compatibility; omitted profile produces LOCAL-only run; 1.1 profile round-trip; unknown logical worker reference rejects before Worker I/O; runtime registry resolves to an `ExecutionPlan`; relocation remains valid because endpoints are external.
- **Acceptance:** No existing 1.0 project breaks, `project.json` contains no endpoint/credential, and a caller can opt into distributed project execution through an additive keyword-only API.

### Phase 7.5B.4 — Release gate and regression matrix

- **Goal:** Treat localhost distributed graph execution as a stable public capability only after the preceding slices are green.
- **Modify:** Regression tests and documentation only, except for narrow bug fixes identified by the matrix.
- **Do not modify:** Public signatures, schema, scheduler semantics, transport protocol, topology execution model.
- **Tests:** LOCAL-only; mixed both directions; two-worker coupling; feedback/self-loop; completion-order inversion; crash-before-commit; public consumer import surface; project 1.0 compatibility if B.3 ships.
- **Acceptance:** Full suite passes on supported Windows Python versions, no production regression changes outside approved slices, and the supported/deferred deployment boundary is documented.

## 6. Risk analysis

| Risk | Current mitigation | Phase 7.5B guardrail |
| --- | --- | --- |
| A Worker fails after native side effects but before a global checkpoint commits | Coordinator read-all barrier and crash tests prevent publication of a partial checkpoint. | Do not add retry/replay/recovery without a separate transactional design. |
| Connection cleanup masks the original failure | Executor and runner aggregate cleanup failures onto a primary `EngineError`. | Add direct lifecycle contract tests before refactoring cleanup. |
| Placement changes local graph behavior | Engine dispatches only when a node resolves to WORKER; local plans retain old runner path. | Keep LOCAL-only regression tests in every slice. |
| Endpoint data makes projects non-portable or leaks future credentials | No project execution plan is currently persisted. | Persist logical placement only; resolve endpoint externally at run time. |
| A future feature bypasses Coordinator scheduling | Graph runner/orchestrator are placement-agnostic and Workers receive only runtime commands. | Do not put routing, graph state, or checkpoint ownership in Worker code. |
| Security scope is misrepresented | Worker subprocess binds loopback; no TLS/auth/LAN support is claimed. | Keep all remote/LAN/cloud work explicitly out of this phase. |

## Historical frozen recommendation

Begin with **Phase 7.5B.1: executor lifecycle contract hardening**. It is the smallest slice that converts the existing concrete public implementation from primarily end-to-end proven to directly contract-proven, without changing production behavior or prematurely committing Project schema and deployment semantics.
