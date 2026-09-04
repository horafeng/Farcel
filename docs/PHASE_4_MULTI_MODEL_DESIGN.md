# Phase 4：多模型架构与全局时间语义（4.0A）

> 状态：**4.0A architecture/time semantics freeze；4.0B graph contracts
> completed。** 4.0B 的声明性 DTO 位于 `farcel.contracts.graph`；它不实现
> `ModelNodeRuntime`、scheduler、`DataRouter`、`validate_graph()` 或
> `run_graph()`。当前公开的单 FMU API 和运行时行为不变。

## 1. 目标、范围和明确非目标

Phase 4 把 Farcel 从单模型 FMU runtime 演进为本机内的多模型集成仿真：

```text
SimulationGraph
  -> SimulationOrchestrator
       -> multiple ModelNodeRuntime
            -> FMU runtime
```

v1 只覆盖 single-machine、同步、blocking 的 multi-FMU integrated
simulation。节点初期可由 FMI 2/3 Co-Simulation 与已支持的 FMI 2 Model
Exchange 提供；FMI 3 Model Exchange 和 Scheduled Execution 仍不是 runtime
scope。

本阶段及其 v1 后续不包含：

- PySide graph editor、project save/load 或 SysML import；
- Simulink、AMESim、ANSYS、Zemax direct adapter；
- model server、distributed execution、RPC、cloud、worker process；
- realtime/HIL、ROM、C++ rewrite；
- FMI 3 Model Exchange、Scheduled Execution、multi-rate scheduler；
- fixed-point / Newton algebraic-loop solver、strong coupling 或 same-time
  propagation。

这些边界防止把规划中的异构平台能力误称为已实现的 Phase 4 runtime。

## 2. 架构与兼容性边界

依赖方向继续冻结为：

```text
GUI / CLI
    ↓
application
    ↓
contracts
    ↑
infrastructure
```

`contracts`、`application`、public config/result 只使用 Python 标准类型和
Farcel-owned DTO/enum/error。FMPy、native FMU object、NumPy、ctypes、CVode
以及 GUI 类型仍只能存在于 infrastructure 或调用端；它们不得穿过该边界。

以下已公开的单模型入口完全兼容，既不删除、改名，也不改变其数值或 stop
语义：

```python
create_backend()
load_fmu()
validate_config()
run_fmu()
export_result()
```

既有低层 Co-Simulation session API 也继续保持其当前范围：

```python
create_session()
initialize()
step()
read_outputs()
terminate()
close_session()
```

它不因 multi-model 或 Model Exchange 而扩张。特别是，不能把 ME 的 solver、
event 或 checkpoint 细节伪装进既有 `step()`。

## 3. 未来内部节点运行时边界

后续 Phase 4 以如下 application-internal abstraction 统一编排方向：

```python
class ModelNodeRuntime(Protocol):
    def initialize(self) -> None: ...
    def set_inputs(self, values: Mapping[str, Any]) -> None: ...
    def advance_to(self, target_time: float) -> None: ...
    def read_outputs(self) -> Mapping[str, Any]: ...
    def terminate(self) -> None: ...
    def close(self) -> None: ...
```

`SimulationOrchestrator` 只能对这一边界操作，不能知道或判断
`doStep`、CVode、FMI 2/FMI 3 lifecycle、continuous state、root event、Event
Mode、Early Return 等实现细节。

规划中的实现映射如下：

```text
CoSimulationNodeRuntime
  -> existing SimulationSession

ModelExchangeNodeRuntime
  -> ModelExchangeSession
  -> existing ModelExchangeCheckpointCoordinator
  -> SolverAdapter / CVode
```

`ModelExchangeCheckpointCoordinator.advance_to(checkpoint)` 是 ME node
runtime 推进的主要基础。Phase 4 不重新实现或复制 Phase 3 的 ME
event/checkpoint semantics。

## 4. 全局时间网格

Phase 4 v1 的 graph 只有一组全局时间字段：

- `start_time`；
- `stop_time`；
- `communication_step`；
- `output_interval`。

所有节点都在同一个 `start_time` 初始化，并且每个已完成的 global macro-step
都使全部节点到达同一个 global checkpoint。建议 v1 validator 要求
`(stop_time - start_time)` 是 `communication_step` 的整数倍；因此 graph API
可以比单模型 `run_fmu()` 更严格。

这项 stricter rule 只属于 graph API：不能改变 `run_fmu()` 现有的 partial
final-step 行为。multi-rate、不相同的节点 communication grid、插值和隐式
时间对齐均不在 v1。

若一个节点不能完整到达目标 checkpoint，整个 graph run 失败；不能产生一部分
节点处于 `t_(k+1)`、另一部分停留在 `t_k` 的成功或 stopped graph result。

## 5. Coupling 和路由时间语义

v1 采用 **explicit Jacobi macro-step、previous-checkpoint value、zero-order
hold**。对每个 `[t_k, t_(k+1)]`，严格执行：

1. 在 `t_k` 读取所有 source node 的完整 output snapshot。
2. 从这个 immutable snapshot 计算所有 `Connection` 的 routed input。
3. 将所有 routed input 设置到其 downstream nodes。
4. 分别令所有 nodes `advance_to(t_(k+1))`。
5. 确认全部 nodes 完整到达 `t_(k+1)`。
6. 读取新的完整 output snapshot。
7. 按 `output_interval` 记录 graph result。
8. 发布以 global checkpoint 为准的 progress。

禁止如下顺序：先推进 A、读取 A、立即路由到 B、再推进 B。那会把节点声明或执行
顺序错误地变成数值语义。尽管 v1 为单线程同步实现并可按 `graph.nodes` 声明顺序调用
节点，immutable previous-checkpoint snapshot 必须确保结果不依赖该顺序。

连接值在整个 macro-step 内 zero-order hold；v1 不在同一 checkpoint 对新输出做
再传播、插值或迭代。

## 6. Feedback 和 algebraic loop

feedback（包括 `A -> B -> A`）及 self-loop 在 v1 合法，但都解释为显式延迟
coupling：

```text
A(t_k) drives B during [t_k, t_(k+1)]
B(t_k) drives A during [t_k, t_(k+1)]
```

因此它不是 algebraic-loop solve。不执行 fixed-point iteration、Newton iteration、
strong coupling 或 same-checkpoint propagation。需要这些能力时，必须作为新的
时间语义和验证矩阵单独设计，不能改变 v1 result 的含义。

## 7. 初始化和第一次路由

所有 native node runtime 先完成 `initialize()`，随后在共同的 `start_time` 读取
完整 initial output snapshot。该 snapshot 必须在第一次 graph connection routing
之前产生。

目标 node 的 `ModelNodeConfig.initial_inputs` 可用于 FMU initialization，也可为
feedback 提供初始猜测。初始化结束后再执行：

```text
source(t0) -> route -> target input held for [t0, t1]
```

在 `t0` 不做 fixed-point 或重复传播。也就是说，connection 的 initial route 覆盖
connection target 在第一个 macro-step 的 held runtime input；`initial_inputs` 并不
提供 same-time feedback 收敛机制。

## 8. Model Exchange routed-input release blocker

graph routing 对 Model Exchange node 的新 input 是 **input event**，不得被
`SimulationOrchestrator` 以如下方式绕过 Phase 3 语义：

```text
ModelExchangeSession.set_inputs()
-> integrate
```

Phase 4.2B 必须首先在 `ModelExchangeCheckpointCoordinator` 增加或抽取一个
application-owned input-application boundary（语义可等价于
`apply_inputs(values)`），复用既有顺序：

```text
set input
-> Event Mode
-> discrete-state iteration
-> Continuous-Time Mode
-> conditional solver reset
```

不允许复制第二份 ME event handling。本桥接是 CS + ME graph runtime 的 release
blocker；它完成前，ME node 不能声称支持 routed graph input。

## 9. Stop、progress 和 deterministic cleanup

一个 `RunControl` 控制整个 graph。若在开始前已请求 stop，返回/抛出既有
`CANCELLED` 语义，且不得实例化任何 native node runtime。

初始化之后，stop 仅在 global checkpoint boundary 生效。若请求在
`t_k -> t_(k+1)` 的任一 native call 内抵达，orchestrator 在没有 runtime error
时完成当前 macro-step 的全部节点推进到 `t_(k+1)`，确认同步后才：

```text
STOPPED -> terminate all nodes -> close all nodes -> partial GraphSimulationResult
```

故 graph stop 是 cooperative/non-preemptive；最坏延迟包括当前 macro-step 剩余的
node native calls。它不能使 `A=t_(k+1), B=t_k, C=t_k` 成为 graph result，也不能
修改单模型 `run_fmu()` 的 stop 语义。

`RunProgress` 规划复用既有 DTO：`current_time` 表示 global checkpoint，
`completed_steps` 只统计已成功完成的 global macro-step，`sample_count` 是已实际
记录的 graph samples。callback 仍在同步 run 调用线程执行。

任一 node runtime error 都失败整个 graph。orchestrator 仍对每个已创建 runtime
best-effort 执行 `terminate()` 与 `close()`；一个 cleanup failure 不得阻止其他
节点清理。保留 primary runtime `ErrorCode`，将 cleanup failures 追加到 details。
可优先复用 `CONFIG_ERROR`、`INITIALIZATION_ERROR`、`INPUT_SET_ERROR`、
`STEP_ERROR`、`OUTPUT_READ_ERROR`、`TERMINATION_ERROR`、`CLEANUP_ERROR`、
`INTERNAL_ERROR`，而不是无必要新增 graph-specific codes。

graph runtime error 的 diagnostics 至少可附加 `node_id`、适用的 `connection`、
`current_time` 和 `phase`。

## 10. 4.0B contract 计划与实现状态

4.0B 已在 `farcel.contracts.graph` 新增如下纯 Farcel/Python DTO；它们保持
声明性，semantic validation 留给 4.1：

```python
PortReference(node_id, variable_name)
Connection(source, target)

ModelNodeConfig(
    parameters,
    initial_inputs,
    input_schedule,
    selected_outputs,
    relative_tolerance,
    execution_interface,
)
ModelNode(node_id, model_path, config)
SimulationGraph(nodes, connections)

GraphSimulationConfig(
    schema_version,
    start_time,
    stop_time,
    communication_step,
    output_interval,
)
```

全局时间字段只属于 `GraphSimulationConfig`，不得复制进每个
`ModelNodeConfig`。Graph validator 将为每个 node 构造 temporary effective
`SimulationConfig`，复用既有 `validate_config()` 与
`resolve_execution_interface()`；不能复制单模型的 default、validation 或 interface
selection logic。

`ModelNodeConfig.selected_outputs` 只选择未来 graph result 要记录的 node output，
不限制 connection dependency。例如 `A.y -> B.u` 时，即使 A 的
`selected_outputs == ()`，后续 runtime 仍必须读取 `A.y` 用于 routing；runtime
也不得为了路由而自动修改 recording selection。

## 11. Port、Connection 与 validation

connection endpoint 使用 `PortReference(node_id, variable_name)`。调用方不能重复
声明 causality、data type、shape 或 unit；这些都从对应 node 的
`ModelMetadata.variables` 解析。

至少验证：

- node id 非空且唯一；
- source/target node 存在，source/target variable 存在；
- source causality 是 `output`，target causality 是 `input`；
- 无 duplicate connection；
- 每个 target input 至多由一个 connection 驱动；
- target `input_schedule` 与 connection driver 不冲突；
- 每个 node 的 resolved execution capability `can_execute`；
- data type、array shape 与 graph time grid 的兼容性。

一个 source 可连接多个 target；一个 target input 不可有多个 source。对连接驱动的
input，`input_schedule` 会与 route 竞争，v1 应在 validation 阶段拒绝，而不是定义
隐式优先级。

## 12. v1 data compatibility

v1 使用 conservative compatibility。最少允许：

| Source / target family | v1 规则 |
|---|---|
| FMI2 `Real` / FMI3 `Float64` | 直接兼容 |
| FMI2 `Integer` / FMI3 `Int32` | 直接兼容 |
| Boolean / Boolean | 直接兼容 |
| String / String | 直接兼容 |
| Enumeration / Enumeration | 直接兼容 |
| 同类型 FMI3 数值宽度 | 直接兼容 |

不做危险 narrowing，例如 `Float64 -> Float32` 或 `Int64 -> Int8`。array shape 必须
完全相同；禁止 scalar/array conversion、broadcasting、implicit reshape。Binary 和
Clock 不作为 graph runtime connection data。

任何未来 widening、unit conversion、enum mapping 或结构参数改变都必须拥有单独的
显式规则与回归，不能被路由器静默转换。

## 13. Graph result 语义（规划）

既有 `SimulationResult` 完全不修改。未来 `GraphSimulationResult` 至少包含：

```text
start_time, stop_time, step_size, completed_steps, final_time,
completion_state, timestamps, node_outputs
```

`node_outputs` 为 nested mapping：

```text
node_id -> variable_name -> tuple of samples
```

不能采用拼接 key（如 `"node.variable"`）。每个 sample 都来自实际记录的 graph
checkpoint；`completed_steps` 只计完全成功的 global macro-step，`sample_count` 只
表示实际记录 samples。v1 不设计 `GraphResultChunk`，既有 `ResultChunk` 继续只属于
`run_fmu()`；graph runtime 也不宣称 bounded-memory execution。

## 14. 未来 public API（规划）

4.0A 不添加 public method。后续只以 additive API 形式公开：

```python
backend.validate_graph(graph, config)

backend.run_graph(
    graph,
    config,
    *,
    control=None,
    on_progress=None,
)
```

本阶段不设计 `export_graph_result()`、`GraphResultChunk`、async API 或 worker API。
任何实现必须保持已有 public 单模型 surface 及其 schema/position compatibility。

## 15. 后续交付顺序

| 阶段 | 交付与必要约束 |
|---|---|
| 4.0A | 本架构、time/coupling/stop 语义冻结；无 runtime 代码 |
| 4.0B | graph contracts 与 contract-boundary tests |
| 4.1 | **Completed**：application-internal `GraphValidator` 仅 inspect metadata，复用 `ValidationReport` 与 temporary effective `SimulationConfig`；无 runtime/public API |
| 4.2A | **Completed**：application-internal `CoSimulationNodeRuntime` 与 factory；单节点 checkpoint boundary，无 graph scheduler/runtime API |
| 4.2B | **Completed**：application-internal `ModelExchangeNodeRuntime` 与 ME routed-input Event Mode bridge；无 scheduler/runtime public API |
| 4.3 | `SimulationOrchestrator` global scheduler；先以 fake runtimes 测试 |
| 4.4 | `DataRouter`：scalar/Boolean/Integer/String/array routing |
| 4.5 | `GraphSimulationResult`、RunControl、RunProgress、error 与 deterministic cleanup |
| 4.6A | real multi-FMU integration regressions |
| 4.6B | public `validate_graph`/`run_graph`、docs/examples/CI hardening |

4.6A 的最小真实集成矩阵为：2-node `A -> B`、3-node `A -> B -> C`、FMI2 +
FMI3 CS、CS + ME、array routing、stop、failure cleanup、repeated run 和
deterministic result。

### 4.1 implementation note

`farcel.application.graph_validation.GraphValidator` 只依赖 `ModelImporter`
port，并且只执行 metadata inspection；它不创建 session、solver 或 scheduler。它复用
既有 `ValidationIssue` / `ValidationReport` 和 node effective
`SimulationConfig` validation，稳定报告 graph timing、identity/import、connection
endpoint、single-driver 与 input-schedule conflict issue codes。

Connection data type 使用 conservative canonical comparison：FMI2 `Real` /
`Integer` 分别与 FMI3 `Float64` / `Int32` 对齐，其余仅允许相同的受支持 type width；
Binary、Clock 和未知 type 稳定拒绝。array shape 通过现有 structural-parameter
effective-shape resolver 比较，而不是静态 metadata shape。cycles/self-loop 仍合法；
4.1 没有 graph runtime 或 public API。

### 4.2A implementation note

`ModelNodeRuntime` 是 `application.node_runtime` 内部 Protocol，不是 public
contract。`CoSimulationNodeRuntime` 只包装既有 `SimulationSession`；它以
`advance_to(target)` 吸收 FMI3 Early Return，只有完整到达 target checkpoint 才
成功返回，并以每个 target 独立的 attempt guard 防止无限 fragment。

runtime 自己维护 node-local `input_schedule` cursor，因此同一 checkpoint 的 update
只消费一次，Early Return retry 不会重复写入。未来 routed values 通过
`set_inputs()` 进入；`selected_outputs` 仍只是 recording selection。创建 session 时，
future runtime factory 必须使用“recorded outputs 加 Connection source dependency”的
effective selected outputs，但不得修改用户的 `ModelNodeConfig`。

4.2A 不生成 `SimulationResult`、`RunProgress`、`ResultChunk` 或 `RunControl` 语义，
也不切换既有 `CoSimulationRunner` / `run_fmu()`。ME node runtime 及 routed-input
Event Mode bridge 由后续 4.2B 完成。

### 4.2B implementation note

`ModelExchangeNodeRuntime` 以既有 `ModelExchangeSession`、`SolverAdapter` 和
`ModelExchangeCheckpointCoordinator` 实现同一个 application-internal
`ModelNodeRuntime` boundary。routed input 只通过
`ModelExchangeCheckpointCoordinator.apply_inputs()` 进入，因此不会出现 raw
`set_inputs()` 后直接 integrate 的绕行。

`apply_inputs()` 把 routed input、当前 checkpoint 已 due 的 node-local schedule 和
FMI time event 合并到同一套 Event Mode/discrete-state/Continuous-Time Mode handling；
ME schedule cursor 仍由 coordinator 独占。reset 保持既有优先级
`NOMINALS_CHANGED`、`CONTINUOUS_STATES_CHANGED`、`OTHER_PROBLEM_CHANGE`；纯 time
event 且没有 state/nominal change 仍不 reset solver（FMPy Issue #882 regression）。

ME node runtime 不产生 result、progress 或 control 语义，也不切换既有
`ModelExchangeRunner` 或 public `run_fmu()`；global scheduler 从 4.3 才开始。

若 frontend 成果已合入 `main`，才可执行 `Phase 4.SYNC`：确认 clean
`phase-4-work`，fetch origin，正常 `merge origin/main`，逐处解决冲突后执行完整
backend tests、frontend/backend smoke 和 `git diff --check`。禁止 rebase 与 force。
