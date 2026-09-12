# Phase 7 — Distributed Execution Foundation

## Status and baseline

**Phase 7.0 — architecture design freeze.** This is the canonical design
record for Phase 7. It was frozen from the verified `origin/main` baseline
`74c8a21cf93f3c4a033e4b678a758af10b22a720`.

Phase 7.0 changes documentation and the `phase-7-work` push-CI trigger only.
It does **not** implement a Worker, RPC, socket runtime, protocol codec,
asset cache, `RemoteNodeRuntime`, `NodePlacement`, or `ExecutionPlan` Python
contract. The default `create_backend()` remains a local backend.

## 1. Current architecture audit

The existing, verified graph path is:

```text
GUI / CLI -> create_backend() -> FarcelEngine.run_graph()
  -> GraphValidator
  -> GraphSimulationRunner
  -> GraphRuntimeBindingsFactory
  -> SimulationOrchestrator + DataRouter
  -> ModelNodeRuntime
       |- CoSimulationNodeRuntime
       `- ModelExchangeNodeRuntime (FMI 2 only)
  -> Farcel ports -> infrastructure.fmpy -> FMU
```

`SimulationOrchestrator` owns the current logical checkpoint loop. It first
initializes every node and reads a complete initial snapshot. Every later
checkpoint derives routes only from the immutable previous snapshot, sets
inputs for every node, advances every node to the same target, reads every
output, and only then commits the next snapshot and completed-step count.
`DataRouter` is a pure connection router and `GraphSimulationRunner` owns graph
results, sampling, global progress, cooperative stop, and cleanup.

`GraphRuntimeBindingsFactory` creates declaration-ordered local runtimes. It
also adds routing source dependencies to a runtime's read set without changing
the caller's `selected_outputs`; routing-only values therefore stay out of
`GraphSimulationResult`.

Project persistence is deliberately separate from graph execution.
`ModelAsset` stores `asset_id`, canonical project-relative `relative_path`, and
`sha256`. `ProjectService` validates the checksum and resolves the relative
path to a coordinator-local absolute path only in a transient graph passed to
the existing runner. `PROJECT_SCHEMA_VERSION` is currently `"1.0"`.

## 2. Goal, scope, and non-goals

Phase 7 will let a graph node's existing application-internal
`ModelNodeRuntime` be substituted by a runtime backed by a separate local or
trusted-LAN Worker, while preserving Phase 4 numerical results exactly. It is
**distributed logical-time execution**, not a real-time system.

Phase 7 excludes hard real-time, HIL, RTOS, physical I/O, cloud platforms,
Kubernetes, Redis, Kafka, RabbitMQ, Celery, service meshes, checkpoint/restart,
SSP, direct-tool adapters, FMI 3 Model Exchange, and Scheduled Execution.
It also does not make a claim of an Internet-safe distributed platform.

## 3. Frozen numerical invariants

The following Phase 4 semantics are immutable for local, remote, and mixed
graphs:

- explicit Jacobi coupling;
- previous-checkpoint zero-order hold;
- one global logical checkpoint grid; and
- feedback and self-loops delayed by exactly one checkpoint.

For each interval `[t_k, t_(k+1)]`, the coordinator must retain this phase
barrier:

```text
previous immutable snapshot
  -> route all connections
  -> set inputs for ALL nodes
  -> advance ALL nodes to t_(k+1)
  -> wait for ALL advances
  -> read outputs from ALL nodes
  -> commit the next snapshot, results, and progress
```

Completion order is never numerical semantics. In particular, a node that
finishes first must not have its current-checkpoint output routed into another
node that has not completed that checkpoint. Future concurrent advancement is
allowed only between the all-set and all-read barriers.

No failure before the read/commit boundary may fabricate a successful
checkpoint or increase `completed_steps`.

## 4. Coordinator and Worker ownership

The Coordinator continues to own `GraphValidator`, graph semantic validation,
global logical time, `SimulationOrchestrator`, `DataRouter`, the previous
snapshot, global barriers, `RunControl`, `RunProgress`, result sampling,
`GraphSimulationResult` aggregation, and global cleanup orchestration.

A Worker owns only asset availability/cache, FMU loading, metadata/runtime
preparation, local Co-Simulation or FMI2 Model Exchange runtime creation, and
the node lifecycle commands `initialize`, `set_inputs`, `advance_to`,
`read_outputs`, `terminate`, and `close`. It must not own a `SimulationGraph`
scheduler, connection routing, `DataRouter`, global result aggregation, or a
second `GraphSimulationRunner`.

## 5. Runtime substitution and composition

`ModelNodeRuntime` is the formal Phase 7 substitution seam:

```text
ModelNodeRuntime
  |- CoSimulationNodeRuntime
  |- ModelExchangeNodeRuntime
  `- RemoteNodeRuntime                 # future, not implemented in 7.0
```

`SimulationOrchestrator` continues to know only this lifecycle protocol; it
must not know sockets, Worker processes, or FMPy. Placement-aware selection is
therefore a future responsibility of `GraphRuntimeBindingsFactory` or an
adjacent application composition component. It must not be a rewrite of the
orchestrator.

## 6. Declarative placement and execution plan

The future Farcel-owned concepts are `NodePlacement`, `ExecutionPlan`, and a
Worker identity/descriptor/endpoint. A placement associates with a graph by
`node_id`; an absent explicit remote placement means LOCAL behavior.

`SimulationGraph` answers what the simulated system is. `ExecutionPlan`
answers where its nodes run. `ModelNode`, `SimulationGraph`, and the
`SimulationProject` 1.0 schema must not contain a host, port, socket, process
handle, Worker runtime object, or any other deployment implementation detail.
No such Python contracts are created in Phase 7.0, and this design provides no
reason to change `PROJECT_SCHEMA_VERSION == "1.0"`.

## 7. Worker identity, lifecycle, and asset preparation

A Worker has an explicit stable identity for a connection/session and assigns
opaque `runtime_id` values to created runtimes. The Coordinator never treats a
runtime ID as a native or public handle. A runtime lifecycle is:

```text
asset available -> CREATE_RUNTIME -> INITIALIZE
  -> (SET_INPUTS -> ADVANCE_TO -> READ_OUTPUTS)*
  -> TERMINATE -> CLOSE
```

Invalid order, unknown `runtime_id`, wrong `worker_id`, malformed data, or a
terminal runtime produces a stable error response; it cannot be interpreted as
success. Cleanup commands are best effort and should be idempotent at the
Worker resource boundary where practical.

Remote Workers must never use the Coordinator's absolute FMU path. Asset
identity is SHA-256 content addressing:

```text
Coordinator asset SHA-256 -> HAS_ASSET(sha256)
  -> HIT: verified worker cache entry
  -> MISS: PUT_ASSET bytes -> worker temporary file -> SHA-256 recheck
           -> atomic promotion into a Worker-owned cache path
```

A pre-staged asset is simply a cache hit; there is no separate mechanism.
The Worker generates its local cache path and may retain verified cache entries
across runs. It removes incomplete temporary asset files. Future non-Project
`run_graph()` preparation may calculate an ephemeral SHA-256 identity from its
local `model_path`, but it must still transfer content rather than a filesystem
path.

## 8. Transport and protocol

The first implementation target is Python standard-library TCP on localhost or
a trusted LAN. The suggested wire format is length-prefixed framing, UTF-8 JSON
control envelopes, and binary asset payloads. Pickle is prohibited.

Every request and response includes at least `protocol_version`, `request_id`,
`worker_id`, and `message_type`; replies correlate to the request ID. The
protocol defines `HELLO`, `PING`/`HEALTH`, `HAS_ASSET`, `PUT_ASSET`,
`CREATE_RUNTIME`, `INITIALIZE`, `SET_INPUTS`, `ADVANCE_TO`, `READ_OUTPUTS`,
`TERMINATE`, and `CLOSE`.

`HELLO` validates the protocol version and Worker identity before lifecycle
work. Unknown message types, incompatible versions, invalid payloads, unknown
runtime IDs, and incorrect lifecycle states are rejected explicitly. Future
protocol code maps remote failures through a structured Farcel `EngineError`
envelope (`code`, `message`, extensible diagnostic `details`) and reconstructs
the stable error on the Coordinator. It never exposes a socket exception,
`multiprocessing.Connection`, FMPy object, traceback object, or native handle
through public contracts or GUI code.

## 9. Timeouts, retry, and failures

Connect and handshake timeouts may be finite. Runtime-operation timeout is
configurable and may be `None`. A timeout is liveness/failure detection, not a
hard-real-time deadline.

Stateful commands are never automatically retried: especially `INITIALIZE`,
`SET_INPUTS`, `ADVANCE_TO`, and `TERMINATE`. If a connection drops after send,
the Coordinator cannot know whether the command executed and must not replay
it. Connection loss, malformed protocol, Worker crash, or operation timeout
makes the current remote runtime terminal, fails the current graph run, blocks
checkpoint commit, and starts best-effort cleanup. Phase 7 has no automatic
Worker restart/replay and no checkpoint/restart.

Existing Farcel `ErrorCode` values are reused, including `TIMEOUT`,
`CANCELLED`, `INITIALIZATION_ERROR`, `INPUT_SET_ERROR`, `STEP_ERROR`, and
`INTERNAL_ERROR`; Phase 7.0 adds no error codes.

## 10. RunControl, RunProgress, crash, and cleanup

`RunControl` remains only on the Coordinator; it is not serialized and no
Python synchronization primitive is sent to a Worker. If stop arrives while a
remote `advance_to()` is in progress, that operation may finish. The
Coordinator observes stop at the existing completed global-checkpoint control
point, then returns a `STOPPED` partial `GraphSimulationResult`. No Phase 7
feature force-kills an FMU or native call.

`RunProgress` is likewise Coordinator-owned and derived from the globally
committed logical checkpoint. Worker wall-clock speed is not public progress.

EOF, Worker process exit, TCP loss, protocol violation, and timeout all fail
the graph without inventing a checkpoint. The Coordinator best-effort
terminates/closes surviving runtimes; a future locally owned child Worker also
receives process cleanup. A Worker best-effort terminates/closes its runtimes
on disconnect or shutdown. Verified cache entries may remain; temp assets may
not.

## 11. Mixed execution, trust boundary, and public boundary

Mixed local/remote graphs retain one Coordinator, one logical clock, one
router, and the same barrier. Local and remote completion timing cannot affect
numeric results.

The first version is a trusted localhost/trusted-LAN prototype, not an
Internet-facing service. Local Workers should bind loopback by default; LAN
listening must be explicit. Protocol validation, frame/message limits, asset
size limits, SHA-256, no pickle, no arbitrary remote filesystem paths,
Worker-generated cache paths, and malformed-message rejection are mandatory.
TLS, authentication, and PKI are explicitly deferred security enhancements.

The permanent consumer boundary remains:

```text
GUI -> create_backend() -> Farcel public API / farcel.contracts
```

GUI code must not see sockets, TCP connections, subprocess handles,
`multiprocessing.Connection`, FMPy, native handles, or Worker implementation
objects. Any future distributed public API is additive and uses Farcel-owned
DTOs. Phase 7.0 changes no frontend code or public API.

## 12. Test and CI strategy

| Stage | Required evidence |
|---|---|
| 7.1 | Pure contract/protocol serialization round trips, invalid-message and boundary tests; no real network required. |
| 7.2 | Localhost out-of-process Worker, ephemeral port, asset cache/transfer, real-FMU one-node lifecycle, and deterministic Windows cleanup. |
| 7.3 | `RemoteNodeRuntime` parity, placement-aware binding, error/timeout/cleanup behavior. |
| 7.4 | Mixed local/remote graph parity; two Workers; local/distributed numerical equality; Jacobi and feedback-delay regressions; deliberately inverted completion order; crash before checkpoint commit. |
| 7.5 | Localhost CI smoke, manual two-machine LAN instructions, health/timeout, public API/project integration, and final docs/examples/CI. |

Every network/process test has an explicit timeout and deterministic cleanup so
CI cannot hang. GitHub-hosted CI proves `127.0.0.1`/localhost only; a real
two-machine LAN is a manual proof and must not be claimed as CI coverage.

## 13. Frozen decisions and deferred work

Frozen decisions are: the Phase 4 barrier is numerical semantics; the
Coordinator owns graph semantics; `ModelNodeRuntime` is the substitution seam;
placement is separate from `SimulationGraph` and Project schema 1.0; assets are
SHA-256 content addressed and transfer-on-miss; TCP/standard-library framing
uses JSON control plus binary asset payloads without pickle; stateful commands
are not replayed; and the initial trust boundary is localhost/trusted LAN.

Deferred capabilities include actual Worker/RPC implementation until 7.1+,
automatic retry/restart, checkpoint/restart, Internet security, cloud
orchestration, real-time/HIL, physical I/O, direct adapters, SSP, FMI3 ME,
Scheduled Execution, strong coupling, and frontend implementation.
