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

## Phase 7.1A 实现状态

Phase 7.1A 已实现 Farcel-owned 的 `PlacementKind`、`WorkerEndpoint`、
`WorkerDescriptor`、`NodePlacement` 与 `ExecutionPlan` contracts，并提供纯
application 层 `ExecutionPlanValidator` 和 `resolve_node_placement()`。它们只用
`node_id` 将部署计划与 `SimulationGraph` 关联；未显式声明 placement 的 node 会解析为
逻辑 `LOCAL`，不会把默认值写回 graph 或 project。

本阶段没有修改 `SimulationGraph`、`SimulationProject` 或
`PROJECT_SCHEMA_VERSION == "1.0"`，也没有修改既有 graph 数值执行链。validator 不做
DNS、网络、文件系统、FMU、asset、Worker capability 或 protocol version 检查。

仍未实现 protocol codec、Worker、RPC、socket、asset transfer、asset cache 或
`RemoteNodeRuntime`。这些内容继续属于后续 Phase 7 子阶段。

## Phase 7.1B 实现状态

Phase 7.1B 已将 `WORKER_PROTOCOL_VERSION` 冻结为 `"1.0"`，并实现
`WorkerMessageType`、request/response envelope、typed payload contracts 和
structured `RemoteError`。`WorkerProtocolValidator` 只验证协议对象自身的版本、
身份、payload、canonical SHA-256、成功/失败状态和 request/response correlation；
它不访问网络、文件系统或 FMU。

`CREATE_RUNTIME` 只携带 `node_id`、`asset_sha256` 与已构造的
`SimulationConfig`，不包含 Coordinator absolute path、`SimulationGraph`、
`Connection` 或 `ExecutionPlan`。Worker 因而仍只会在后续实现中按自身 cache 的
content identity 准备 runtime，不承担 graph routing 或 scheduler。

仍未实现 JSON codec、binary framing、socket/TCP、Worker、RPC、asset transfer、
asset cache 或 `RemoteNodeRuntime`。本阶段协议对象只是 Python immutable DTO，
不包含传输或 Worker lifecycle state machine。

## Phase 7.1C 实现状态

Phase 7.1C 已实现严格 UTF-8 standard JSON codec：Worker request/response、
`RemoteError`、typed payload、`SimulationConfig` 与通用 Farcel value 都可稳定
round-trip。codec 使用 `allow_nan=False`，拒绝 duplicate key、未知字段、非法 enum、
非法 `ErrorCode` 与不符合 message payload 语义的输入；不使用 pickle。

非有限 float 使用显式 Farcel tag；通用 Mapping 使用独立 entries 容器，因此用户 Mapping
不会与 float tag 产生歧义。length framing 已冻结为 4-byte unsigned big-endian header；
control frame 上限为 1 MiB，asset binary frame 上限为 512 MiB。PUT_ASSET 的 binary body
只可作为 raw length-prefixed bytes，绝不进入 JSON。

wire codec 属于 infrastructure，protocol semantic validator 属于 application；两者通过
contracts-owned 的 `WorkerProtocolSemanticValidator` port 解耦。infrastructure 不直接
import application，永久依赖方向继续为 `application -> contracts <- infrastructure`。

Phase 7.1 至此完成的是 contracts、protocol semantics 与 wire codec/framing，不是
distributed runtime。仍未实现 socket/TCP、Worker process、asset cache、Worker/RPC handler、
`RemoteNodeRuntime` 或 distributed graph execution。

## Phase 7.2A 实现状态

Phase 7.2A 已实现 Worker 自有的 content-addressed FMU asset cache。cache key 是
canonical lowercase SHA-256，cache path 完全由 Worker 按
`<cache_root>/assets/<sha256>.fmu` 生成，不接受 Coordinator path、原始文件名或调用方提供的
filename。写入前会校验 bytes 的 SHA-256，并复用 512 MiB asset size limit。

cache hit 会重新以流式 SHA-256 验证既有文件；corrupted entry 不会被信任，已验证的新内容可用
同目录 temporary file、flush、fsync 与 atomic replace 修复。失败时会 best-effort 清理 temp
file，symlink 与非普通文件不会作为可信 cache hit。

仍未实现 socket/TCP、Worker process、protocol handler、runtime registry、asset transfer handler、
`RemoteNodeRuntime` 或 distributed graph execution。

## Phase 7.2B 实现状态

Phase 7.2B 已实现 transport-independent 的 `WorkerApplicationService`、
`WorkerRuntimeFactory` 与 process-local runtime registry。`CREATE_RUNTIME` 只通过
`WorkerAssetStore.resolve_asset()` 取得 Worker 本机 SHA cache path，随后重新验证
`SimulationConfig`，并复用既有 Co-Simulation / FMI2 Model Exchange runtime factories；不接收
Coordinator model path。

registry 使用 opaque runtime ID 管理 CREATED、INITIALIZED、FAILED 与 TERMINATED 状态。Worker
只执行单个 runtime 的 initialize、set_inputs、advance_to、read_outputs、terminate 与 close；
FAILED runtime 仅可 cleanup，shutdown 会尽力清理全部已登记 runtime。业务错误会转换为 structured
`RemoteError`，Worker 不拥有 `SimulationGraph`、`DataRouter` 或 global scheduler。

仍未实现 socket/TCP、Worker process、`WorkerClient`、`RemoteNodeRuntime` 或 distributed graph
execution。

## Phase 7.2C 实现状态

Phase 7.2C 已实现 localhost TCP transport。v1 server 仅绑定 `127.0.0.1`，支持 `port=0`
ephemeral endpoint；每条连接必须先完成 HELLO，随后在持久连接中串行 request/response。control
frame 继续使用现有 1 MiB 限制，PUT_ASSET 使用 JSON control frame 加独立 512 MiB binary frame。

transport 在读取 body 前先读取并验证 4-byte header；connect 与 handshake timeout 有限，普通
operation timeout 可配置或为 `None`。response correlation 会校验 request ID、Worker ID、message
type 与 protocol version；timeout、断连和 malformed response 不会触发 retry 或 stateful replay。
断连后 server 会 best-effort shutdown 当前 Worker session，但仍可接受新的 HELLO connection。

timeout 参数现明确要求 positive finite number；`NaN`、`Infinity`、布尔值和非正值会稳定拒绝，
而 `operation_timeout=None` 保持允许。localhost TCP regression coverage 已覆盖 timeout、response
correlation、oversized/truncated frame、断连 cleanup、server recovery 与无 retry/replay 语义。

仍未实现 subprocess Worker、LAN bind、TLS/auth、`RemoteNodeRuntime` 或 distributed graph execution。

## Phase 7.2D 实现状态

Phase 7.2D 已提供真实独立 Python Worker subprocess，可通过 `python -m farcel.worker`
启动。child composition root 会组装 Worker asset cache、`WorkerApplicationService`、
`WorkerRuntimeFactory`、既有 FMPy adapters、codec 与 `TcpWorkerServer`；child 固定绑定
`127.0.0.1`，以 `port=0` 交由 OS 分配 endpoint。

Worker 会在 stdout 输出唯一一行 strict JSON readiness；launcher 严格验证 schema、protocol、
worker identity、loopback host 和 port，并对 startup timeout、early exit 与 malformed readiness
提供稳定错误。stderr 会持续消费且只保留有限 diagnostic tail。正常 TCP session disconnect 会触发
Worker shutdown 并令 locally-owned child 自然退出。stdout readiness 只是 bootstrap channel，
不替代 TCP HELLO；parent-owned stdin EOF 是正常的 graceful process shutdown signal，child 的
EOF watcher 会停止 server，随后 finally 会 best-effort `service.shutdown()`。`terminate` 与 `kill`
只会在 stdin EOF 的有界等待超时后作为 emergency fallback；最终仍无法回收 child 会返回稳定的
`CLEANUP_ERROR`，它们不是 FMI graceful cleanup。

已证明 HELLO、PING、HAS_ASSET 与 PUT_ASSET 可真实跨 process 执行。verified asset cache 可跨
Worker process 保留；runtime registry、runtime ID、native instance 与 connection session 不跨 process
保留。本阶段仍未执行 real-FMU runtime lifecycle proof，未实现 `RemoteNodeRuntime`、distributed graph、
LAN bind 或 TLS/auth。

## Phase 7.2E 实现状态

Phase 7.2E 使用仓库中的真实 `VanDerPol.fmu` 完成独立 Worker subprocess proof。FMU 由
Coordinator 计算 SHA-256，并经 `PUT_ASSET` 写入 Worker 自有 cache；`CREATE_RUNTIME` 只携带
`node_id`、`asset_sha256` 和 `SimulationConfig`，不携带 Coordinator path。

FMI2 Co-Simulation 与 FMI2 Model Exchange（含 CVode）均已通过真实 TCP lifecycle：create、
initialize、read、`SET_INPUTS({})`、两次 advance、terminate 与 close。remote output 会与 local
backend baseline 比较；runtime close 后旧 ID 不再存在，但 Worker TCP session 仍可继续 PING。另有
active Model Exchange runtime proof：parent-owned stdin EOF 会让 child best-effort shutdown runtime，
并正常退出，不把 `terminate`/`kill` 当作正常 FMI cleanup。

这些 proof 会在 Windows Python 3.10/3.13 CI 真正运行。尚未实现 `RemoteNodeRuntime`、Worker 到
`SimulationGraph` 的 remote binding、distributed graph execution、LAN、TLS 或 auth。

## Phase 7.3A 实现状态

Phase 7.3A 已新增 Coordinator-side `WorkerRpcClient`，它只依赖 contracts-owned
`WorkerTransportClient`：负责生成 request ID、构造 Worker request，以及把业务 `RemoteError`
恢复为保留 code、message 和 details 的 `EngineError`。它不执行 retry、reconnect 或 replay。

`RemoteNodeRuntime` 已作为 `ModelNodeRuntime` 兼容的本地代理，覆盖 initialize、set inputs、
advance、read outputs、terminate 和 close。proxy surface 上 initialize、terminate 与 close 保持幂等；
empty set inputs 是 no-op。ordinary remote failure 后 proxy 进入 FAILED，仅允许 terminate/close；
CLOSE failure 也不会 replay。runtime close 只发送该 runtime 的 CLOSE，绝不关闭可由多个 runtime
共享的 Worker connection。

本阶段尚未实现 asset staging、CREATE_RUNTIME factory、ExecutionPlan graph binding、
`GraphRuntimeBindingsFactory` remote branch 或 distributed graph execution。

## Phase 7.3B 实现状态

Phase 7.3B 已实现 Coordinator-side `WorkerAssetStager`。`model_path` 只在 Coordinator 本地使用；
它以 streaming SHA-256 形成 asset identity，先查询 Worker cache，hit 时不发送 bytes，miss 时才发送
一次 `PUT_ASSET`。upload 前会重新验证 bytes 的 size 与 SHA-256，若 staging 期间 source 发生变化会
稳定拒绝，不会 retry、reconnect 或 replay。

`RemoteNodeRuntimeFactory` 已通过 asset staging 和 `CREATE_RUNTIME` 返回未初始化的
`RemoteNodeRuntime`。factory 不 connect/close Worker connection，不自动 initialize，且 Worker request
中不包含 Coordinator path；CREATE_RUNTIME 继续只携带 node ID、asset SHA-256 与 config。

本阶段尚未接入 ExecutionPlan、未修改 `GraphRuntimeBindingsFactory`，也尚未实现 distributed graph
execution。

## Phase 7.3C 实现状态

`GraphRuntimeBindingsFactory` 已支持 placement-aware binding，而既有的
`create(graph, config)` 保持完全 local-only 且向后兼容。新的 plan 路径会在任何 runtime 创建前验证
`ExecutionPlan`；未声明 placement 的 node 逻辑上默认为 LOCAL，不会写回或修改 graph、plan。

LOCAL node 继续复用既有 importer 与 CS/ME runtime factories。WORKER node 通过以 worker ID 为 key 注入的
`RemoteRuntimeProvisioner` 创建，不会在 binding factory 中调用 Coordinator-local importer；缺少 provisioner
会稳定失败，绝不静默降级为 LOCAL。local/remote node 都使用同一份 effective routing output read set，且
bindings 始终保持 graph declaration order。

mixed creation 的部分失败会关闭此前已创建的 local/remote runtime，并保留 primary error 与 cleanup failure
聚合；node runtime cleanup 不拥有 Worker connection cleanup。本阶段未修改 `SimulationOrchestrator`、
`DataRouter` 或 `GraphSimulationRunner`，尚未真正运行 mixed/distributed graph，也尚未将
`WorkerDescriptor.endpoint` 自动 composition 成 live connection；Engine/Backend public API 不变。

## Phase 7.3D 实现状态

已使用真实 `VanDerPol.fmu` 完成 single-node remote graph 的完整
`GraphSimulationRunner` proof。`ExecutionPlan` 的 WORKER placement 经
`GraphRuntimeBindingsFactory` 产生 `RemoteNodeRuntime`，而
`GraphSimulationRunner` 与 `SimulationOrchestrator` 无需知道 Worker、RPC 或 process；既有 graph
lifecycle 自动驱动 initialize、read、advance、结果采样、terminate 与 close。

remote graph result 与 local graph baseline 在 timestamps、completed steps、completion state 和 `x0`
数值上保持一致。STOPPED 路径同样会清理 remote runtime；runner cleanup 后 Worker connection 仍可 PING，
connection ownership 继续位于外层 composition。`RunControl` 仍只属于 Coordinator，不会发送到 Worker。

本阶段未实现 mixed local/remote coupling、two Workers、feedback/Jacobi distributed parity、parallel
advance 或 Worker crash before checkpoint commit；未修改 `GraphSimulationRunner`、
`SimulationOrchestrator`、`DataRouter`，Engine/Backend public API 仍未变化。Phase 7.3 至此完成。

## Phase 7.4A 实现状态

已使用真实 `Feedthrough-fmi2.fmu` 运行 LOCAL A → WORKER B mixed graph：A 在 Coordinator 的 FMPy
runtime 中执行，B 在独立 Worker subprocess 中执行。A→B 的 routed value 会通过
`RemoteNodeRuntime.set_inputs()` 和 TCP `SET_INPUTS` 真实进入 Worker。mixed 与 all-local 在 completion
state、timestamps、completed steps 与 samples 上保持一致，B output 为 `(0, 2, 2)` 的浮点等价值。

该序列表明初始化阶段没有提前 coupling，checkpoint routing 使用 previous immutable snapshot；A 的
routing-only output 不会泄漏到最终 result。将 B 声明在 A 之前仍保持同样数值，证明本阶段的 graph
declaration / sequential invocation order 不会污染该 forward-coupling 结果；这不等同于已验证 wall-clock
completion-order inversion。

本阶段尚未测试 WORKER→LOCAL、two Workers、feedback/self-loop 或 Worker crash before checkpoint commit；
未修改 `SimulationOrchestrator`、`DataRouter`、`GraphSimulationRunner`，Engine/Backend public API 不变。

## Phase 7.4B 实现状态

已运行真实 WORKER A → LOCAL B `Feedthrough-fmi2.fmu` mixed graph：A 在独立 Worker subprocess，B 在
Coordinator 的真实 FMPy runtime。A 的 routing output 经 TCP `READ_OUTPUTS` 返回 Coordinator，
`DataRouter` 从 A 的 previous snapshot 路由到 local B；B 在两个 checkpoint 收到的 routed input 都是 `2.0`。

mixed 与 all-local 在 completion state、timestamps、steps 与 samples 上一致，B output 为 `(0, 2, 2)` 的
浮点等价值；remote A 的 routing-only output 不进入最终 result。将 B 声明在 A 之前仍保持 parity，证明
declaration / sequential invocation order 不影响该 WORKER→LOCAL forward-coupling 数值，且不宣称已验证
真实 wall-clock completion-order inversion。

结合 Phase 7.4A，本阶段已覆盖 mixed coupling 的 LOCAL→WORKER 与 WORKER→LOCAL 两个跨边界方向。尚未
测试 WORKER→WORKER two-worker coupling、feedback/self-loop、真实 wall-clock completion-order inversion 或
Worker crash before checkpoint commit；未修改 `SimulationOrchestrator`、`DataRouter`、
`GraphSimulationRunner`，public Engine/Backend API 不变。

## Phase 7.4C 实现状态

已使用两个真实、相互独立的 Worker subprocess 运行 WORKER A → WORKER B
`Feedthrough-fmi2.fmu` graph：A 与 B 分别位于不同 PID、不同 worker ID 和不同 TCP session，
Coordinator 不创建任何 local FMU runtime。A 的 routing source 通过 TCP `READ_OUTPUTS` 返回
Coordinator；Coordinator `DataRouter` 使用 previous immutable snapshot，再经 Worker B 的 TCP
`SET_INPUTS` 写入 target runtime。Worker A 不直接连接 Worker B，Workers 互相不知道对方存在。

两个 Worker 分别拥有 content-addressed asset cache。two-worker 与 all-local 在 completion state、
timestamps、completed steps 与 samples 上保持一致，B output 为 `(0, 2, 2)` 的浮点等价值；A 的
routing-only output 不会进入 `GraphSimulationResult`。将 B 声明在 A 之前仍保持 numerical parity，
证明该 two-worker forward coupling 不受 graph declaration / sequential invocation order 影响；这不等同于
已验证真实 wall-clock completion-order inversion。

graph run 后两个 Worker connection 都仍可 PING；关闭 Worker A 后 Worker B 仍可继续 PING，证明 node
runtime lifecycle 不拥有任一 connection 的生命周期。结合 Phase 7.4A/B/C，LOCAL→WORKER、
WORKER→LOCAL 与 WORKER→WORKER 三种跨 placement coupling 均已覆盖。

尚未验证 feedback/self-loop、真实 wall-clock completion-order inversion 或 Worker crash before checkpoint
commit；未修改 `SimulationOrchestrator`、`DataRouter`、`GraphSimulationRunner`，public Engine/Backend API
仍未变化。

## Phase 7.4D 实现状态

已用两个真实独立 Worker subprocess 执行 A↔B feedback graph，两个 node 均使用真实
`Feedthrough-fmi2.fmu`：A initial input 为 1，B initial input 为 2。all-local 与 two-worker
数值保持 parity，预期轨迹分别为 A=`(1, 2, 1, 2)`、B=`(2, 1, 2, 1)`。

每个 checkpoint 中，A 的 input 都来自 B 的 previous immutable snapshot，B 的 input 都来自 A 的
previous immutable snapshot。wire-level `SET_INPUTS` 序列直接证明 one-checkpoint delay：A 依次收到
`2, 1, 2`，B 依次收到 `1, 2, 1`。因此初始化阶段没有提前 feedback coupling，也没有
same-checkpoint algebraic feed-through。两个 Worker 都真实返回 initial 加三个 checkpoint 的
`READ_OUTPUTS`；反转 graph declaration order 后仍保持 parity。

Workers 之间没有 direct communication；Coordinator 继续拥有 global logical time、routing 和 checkpoint
barrier。未修改 `SimulationOrchestrator`、`DataRouter` 或 `GraphSimulationRunner`，public
Engine/Backend API 仍未变化。尚未验证 remote self-loop、真实 wall-clock completion-order inversion 或
Worker crash before checkpoint commit。

## Phase 7.4E 实现状态

已用一个真实独立 Worker subprocess 运行单 node remote self-loop graph，使用一个真实
`Feedthrough-fmi2.fmu` runtime。self-loop 是两条 intra-node cross-coupled edge：
continuous output → discrete input，以及 discrete output → continuous input。初始 continuous input 为
1、discrete input 为 2；预期轨迹为 continuous output = `(1, 2, 1, 2)`、discrete output =
`(2, 1, 2, 1)`，all-local 与 remote 保持 numerical parity。

每个 checkpoint 的两个 `SET_INPUTS` value 都来自同一 previous immutable snapshot：continuous input
依次为 `2, 1, 2`，discrete input 依次为 `1, 2, 1`。这直接证明 remote self-loop 保持
exactly-one-checkpoint delay，初始化阶段没有提前 coupling，也没有 same-step algebraic feed-through。
反转 connection declaration order 后轨迹不变。

即使 source 与 target 属于同一 remote runtime，routing 仍经过 Coordinator 的 global logical time、
`DataRouter` 和 checkpoint barrier；不存在 Worker-local self-loop shortcut，Worker 也不知道 graph 或
self-loop。未修改 `SimulationOrchestrator`、`DataRouter`、`GraphSimulationRunner` 或 Worker protocol。
尚未验证真实 wall-clock completion-order inversion 或 Worker crash before checkpoint commit，public
Engine/Backend API 仍未变化。

## Phase 7.4F 实现状态

已新增 application-internal `NodeAdvanceExecutor` seam。`SimulationOrchestrator` 仍拥有 previous
immutable snapshot、routing、all-set barrier、all-read barrier 与 checkpoint commit；executor 只负责一个
checkpoint 的 advance-all execution policy。默认 `SequentialNodeAdvanceExecutor` 按 node declaration order
调用 advance，因此既有 local/remote graph execution 行为保持不变。

`GraphSimulationRunner` 仅提供 application-internal optional injection，并将 executor 交给
`SimulationOrchestrator`；public Engine/Backend API 未变化。本阶段没有实现 production thread pool、
Worker-aware concurrency，也没有改变 TCP 或 Worker protocol。executor 的普通异常会稳定转换为
`INTERNAL_ERROR`，而通过 orchestrator callback 产生的 runtime advance error 继续保留 node 与 advance
phase context。

本阶段尚未声称已验证 wall-clock completion-order inversion。下一阶段将通过两个真实 Worker 与注入的
受控 concurrent executor 强制完成顺序反转；Worker crash before checkpoint commit 仍未验证。

## Phase 7.4G 实现状态

已使用两个真实独立 Worker subprocess、真实 `Feedthrough-fmi2.fmu` A↔B feedback graph，以及 7.4F
正式 `NodeAdvanceExecutor` injection seam 完成 Coordinator-observed completion-order proof。concurrent
executor 仅存在 integration test；两个 remote node advance 在不同线程执行，并使用有限超时的 Event/Barrier
确定性控制 timing，不使用 sleep。

当 declaration/invocation 为 A→B 时，每个 checkpoint 的 Coordinator-observed `advance_to()` completion
均为 B→A；对称的 B→A declaration/invocation case 则为 A→B completion。这里的 proof 只描述 Coordinator
application 层观察到的 `ModelNodeRuntime.advance_to()` return order，不宣称测量 Worker native FMU 的 CPU
internal finish timestamp。

尽管 completion order 被反转，all-local 与 two-worker concurrent result 仍保持 numerical parity：
A=`(1, 2, 1, 2)`、B=`(2, 1, 2, 1)`。current checkpoint output 不会提前成为另一个 node 的 routing source；
read-all 与 checkpoint commit 仍等待 advance-all barrier，因此 feedback 继续保持 previous-snapshot
exactly-one-checkpoint delay。Worker A/B 之间没有 direct communication，所有 coupling 仍经 Coordinator。

本阶段未新增 production thread pool 或 Worker-aware concurrency，未修改 Worker protocol，public
Engine/Backend API 不变。Worker crash before checkpoint commit 仍未验证。

## Phase 7.4H 实现状态

已使用两个真实独立 Worker subprocess 与真实 `Feedthrough-fmi2.fmu` A↔B graph，使用
`os.kill(pid, signal.SIGTERM)` 触发真实 abrupt process termination；没有使用 `launcher.close()`、mock
transport error 或 fake `EngineError` 模拟 crash。

第一个 proof 中，A/B 都已经成功 `ADVANCE_TO(0.01)`，但 A 在 checkpoint output read 前崩溃。尽管两个
remote FMU 都已有 target-time side effect，read-all 未完成，因此 global checkpoint 不会 commit：
`GraphSimulationRunner` 抛出 primary `EngineError` 而不返回 `GraphSimulationResult`，只发布 initial
`RUNNING` progress，B 的 checkpoint `READ_OUTPUTS` 不会发生。存活的 B runtime 仍被 best-effort
terminate/close，且 runtime cleanup 后 B connection 仍可 PING；A 的 cleanup failure 如发生只附加为
`cleanup_failures`，不会覆盖 primary error。

第二个 proof 中，A 在首个 stateful `ADVANCE_TO` 前崩溃。该 advance 只尝试一次且无成功 response；没有
retry、replay、reconnect、Worker restart 或 checkpoint recovery。B 已收到本 checkpoint routed input，但因 A
advance failure 不会执行 `ADVANCE_TO`；同样不会发布 `.01` sample。RunControl/STOPPED 不参与 crash failure。

未修改 `SimulationOrchestrator`、`GraphSimulationRunner`、`RemoteNodeRuntime`、`TcpWorkerClient` 或 Worker
protocol，public Engine/Backend API 未变化。Phase 7.4 的正常拓扑、completion-order 与 crash failure semantics
至此均已有真实 proof。

## Phase 7.5A 实现状态

已新增纯 application-internal `DistributedGraphExecutor` seam，并将 `ExecutionPlan` 以 additive
keyword-only `execution_plan` 参数接入 `FarcelEngine.run_graph()` 与公开 `SimulationEngine` contract。
`FarcelEngine.validate_execution_plan()` 复用纯 `ExecutionPlanValidator`，在不加载 FMU、不创建 runtime、
不访问 Worker 的前提下返回验证报告，或以统一的 `CONFIG_ERROR` issue schema 报告无效计划。

未传 `execution_plan`、空计划、显式 LOCAL placement，以及仅声明但未被任何 node 使用的 Worker，均继续走原有
local `GraphSimulationRunner` 路径。只有解析后确有 WORKER placement 时才会选择 distributed seam；未注入
executor 会稳定报 `NOT_IMPLEMENTED`，绝不 silent fallback 到 local。预启动取消仍先于 graph 和计划验证。

本阶段没有把 `create_backend()` 连接到 TCP Worker，也没有新增 Worker/TCP/protocol 代码或改变默认 local
execution。后续 concrete distributed composition 可实现该 seam；真实 public localhost distributed execution
留待 Phase 7.5B。

## Phase 7.5B 实现状态

`create_backend()` 现在在 top-level composition root 配置 concrete `TcpDistributedGraphExecutor`，但创建
backend 本身不产生网络 I/O。未传计划、空计划、显式 LOCAL placement，以及仅声明但未被 node 使用的 Worker
均继续使用原有 local graph path；只有实际 WORKER placement 才会从 `WorkerDescriptor` endpoint 建立 TCP
composition。backend 不会自动 spawn Worker，endpoint 描述的 Worker 必须已存在。

executor 仅为实际使用的每个 Worker 建立一条 `TcpWorkerClient`/`WorkerRpcClient` session，并复用现有
HELLO、connect 后 PING preflight、`WorkerAssetStager`、`RemoteNodeRuntimeFactory`、
`GraphRuntimeBindingsFactory.create_with_plan()` 与 `GraphSimulationRunner`。graph runtime cleanup 完成后，
executor 才关闭本次拥有的 TCP connections；失败与 partial connect 也会 best-effort 清理，且不 retry、reconnect
或 restart。

Windows CI 的 public localhost E2E 使用真实 subprocess Worker 验证 mixed LOCAL→WORKER 和 two-worker
WORKER→WORKER graph：public consumer 仅使用 `create_backend()` 与 `farcel.contracts`，Worker/TCP/FMPy
内部类不进入 consumer path。数值 scheduler、Worker protocol、contracts DTO 与 public `create_backend()`
signature 未修改。Project distributed integration、LAN bind/manual two-machine，以及 public health/timeout
configuration 尚未完成。
