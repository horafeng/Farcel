# Phase 3：Model Exchange 与 Solver Runtime 设计冻结（3.0A / 3.0B / 3.1 / 3.2 / 3.2.1）

> 状态：3.0A 已冻结设计；3.0B 已冻结 CS 行为并建立公共 contract 骨架；3.1 已抽取 application 层 Co-Simulation runner；3.2 已实现 FMI2 Model Exchange session adapter 与 continuous-time primitive；3.2.1 已补齐 ME interface-specific native binary guard。**尚未实现 Model Exchange runtime**，不改变现有 FMI 2/3 Co-Simulation（CS）数值行为、公开 `run_fmu()` 调用形态或版本号。

## 1. 目标、范围与非目标

Phase 3 的目标是在保持 Farcel 公共 API、结果语义和 GUI/CLI 调用方式稳定的前提下，加入 **FMI 2.0 Model Exchange（ME）** 执行能力。ME 中时间推进、连续状态、导数、事件和数值积分器由 importer/运行时负责，因此它不能复用 CS 的 `doStep()` 实现。

首个正式 solver 候选为 CVode；其实现必须仍可被未来的 RK45、BDF 或 C/C++ 后端替换。

3.0A 的非目标如下：

- 不实现 FMI 2 ME 正式 runtime、CVode adapter、RK45 或 BDF。
- 不增加 SciPy，不调用 `simulate_fmu(..., solver="CVode")` 作为 Farcel runtime。
- 不修改 GUI、CLI 的核心执行逻辑、版本号或现有 CS 数值行为。
- 不支持 FMI 3 ME、Scheduled Execution、多 FMU、worker 或强制中断 native solver。

## 2. 当前基线与需要解决的架构问题

当前 `SimulationSession.step(current_time, step_size)` 的语义是 CS 的一次 FMI `doStep()`：FMU 自己负责内部积分，Farcel 只处理 communication point、Early Return 和结果采样。将 ME 伪装为同一 `step()` 会把 solver 内部时间推进、root/state event、Event Mode 和离散迭代错误地压缩为 CS 语义，最终形成难以维护的大型分支循环。

当前 `FarcelEngine.run_fmu()` 同时承担公共生命周期、CS communication target、采样、stop/progress/chunk 与 cleanup。后续重构必须先用 characterization test 固定当前 CS 行为，再把该循环拆为 interface-specific runner；3.0A 不进行这项代码重构。

依赖方向保持不变：

```text
GUI / CLI
    ↓
application（FarcelEngine、runner、validation）
    ↓
contracts（DTO、port、稳定错误）
    ↑
infrastructure（FMPy FMI binding、CVode/SUNDIALS adapter）
```

FMPy、SUNDIALS/CVode、NumPy、ctypes、FMU instance 与 native handle 只允许存在于 `src/farcel/infrastructure/fmpy/`；它们不得出现在 public contract、application、GUI 或 `SimulationResult` 中。

## 3. Runner 分层

`FarcelEngine.run_fmu()` 仍是 GUI/CLI 的唯一同步高层入口，但只负责公共编排：加载 metadata、验证 config、选择执行接口并分派 runner。interface-specific runner 负责其运行生命周期、稳定错误映射和 cleanup；engine 不会继续增长为包含 ME 细节的循环。

```text
FarcelEngine.run_fmu()
  ├─ interface selector
  ├─ CoSimulationRunner
  │    └─ 现有 SimulationSession / doStep 语义（逐步迁移且结果不变）
  └─ ModelExchangeRunner
       ├─ ModelExchangeSession（FMI 生命周期与问题回调）
       ├─ SolverAdapter（数值推进）
       └─ 事件、checkpoint、采样与共享运行可观测性
```

`CoSimulationRunner` 负责 communication target、`doStep()`、FMI 3 Early Return、CS Event Mode 与现有 variable communication-step 规则。`ModelExchangeRunner` 负责连续状态、导数、状态/时间/input event、FMI Event Mode、离散状态迭代、`completedIntegratorStep()`、solver reset 和容差。两者共享的只应是 Farcel-owned 生命周期、`RunControl`、进度通知、canonical result accumulator、`ResultChunk` 和 stable error mapping。

## 4. 接口选择与向后兼容 contract

冻结的后续 additive 字段为：

```python
SimulationConfig.execution_interface: InterfaceType | None = None
```

该字段附加在所有既有位置参数字段之后（`input_schedule` 之后），并由旧位置参数构造回归测试保护。3.0B 已添加该字段，但只开放安全 validation 语义，不创建 ME session 或 solver。

后续实现的选择规则：

| 配置 | 选择 |
|---|---|
| `None`，FMU 同时含 CS 与 ME | 保持旧行为，优先 CS |
| `None`，只有一个 Farcel 当前支持且可执行的接口 | 自动选择该接口 |
| 显式 `CO_SIMULATION` | 要求 CS 可执行；不回退到 ME |
| 显式 `MODEL_EXCHANGE` | 要求 FMI2 ME runtime 已启用且该接口可执行；不回退到 CS |
| 显式 SE 或接口不存在/不可执行 | validation 返回稳定 `UNSUPPORTED_INTERFACE` 或平台相关错误 |

metadata 仍表示“已解析的接口”与“当前可执行接口”的区别。ME 支持落地前，现有 `executable_interface=CO_SIMULATION` 策略不变；不能因为 metadata 含 ME 就错误标记为可执行。

3.0B 中，`None` 与显式 `CO_SIMULATION` 继续走并验证既有 CS 路径；显式 `MODEL_EXCHANGE` 在 native session 创建前以 `UNSUPPORTED_INTERFACE` 稳定拒绝，不回退到 CS；显式 `SCHEDULED_EXECUTION` 同样保持不支持。

## 5. 三种时间尺度的最终语义

| 概念 | CS | ME | 不能被解释为 |
|---|---|---|---|
| `communication_step` | 一次 `doStep()` 的通信步长 | Farcel 外层 control/checkpoint interval | CVode 数值内部步长 |
| solver internal step | FMU 内部实现细节 | CVode 等 solver 自适应决定，可在一个 checkpoint 前执行多个 | `completed_steps` 或 canonical sample |
| `output_interval` | 已到达 communication point 的 canonical 采样间隔 | 已到达 checkpoint 的 canonical 采样间隔 | solver step 或插值开关 |

ME 的 checkpoint 用于 cooperative stop、progress、输入计划边界与外层推进统计。第一版维持“不插值”原则：`output_interval` 必须与 Farcel checkpoint 网格兼容，只有实际到达采样点才读取输出并追加 `SimulationResult`/`ResultChunk`。将来的 interpolation 是单独需求，不能借 CVode 的内部状态悄悄改变 canonical result。

ME 中 `completed_steps` 只在完整到达一个 Farcel checkpoint 后加一。solver 因 root/state event 或 time event 提前返回时，处理事件后仍继续向原 checkpoint 推进；这些内部推进不增加该计数，也不单独产生普通 sample/chunk。

## 6. ModelExchangeSession 与 problem boundary

3.0B 已在 `contracts/ports.py` 中增加独立的 `ModelExchangeSession`，而不是扩张已有的 CS `SimulationSession`。它公开的值均为 Farcel/标准 Python 类型，例如不可变 `tuple[float, ...]`、`bool` 与 Farcel dataclass；不暴露数组库、FMPy object 或 native pointer。

冻结的职责形状如下（名称可作小幅实现调整，语义不得缩水）：

```python
class ModelExchangeSession(Protocol):
    def initialize(self) -> ModelExchangeInitialization: ...
    def set_inputs(self, values: Mapping[str, Any]) -> None: ...
    def set_time(self, time: float) -> None: ...
    def get_continuous_states(self) -> tuple[float, ...]: ...
    def set_continuous_states(self, states: tuple[float, ...]) -> None: ...
    def get_derivatives(self) -> tuple[float, ...]: ...
    def get_event_indicators(self) -> tuple[float, ...]: ...
    def completed_integrator_step(self) -> IntegratorStepResult: ...
    def enter_event_mode(self) -> None: ...
    def update_discrete_states(self) -> DiscreteStateUpdate: ...
    def enter_continuous_time_mode(self) -> None: ...
    def read_outputs(self) -> Mapping[str, Any]: ...
    def terminate(self) -> None: ...
    def close(self) -> None: ...
```

`ModelExchangeInitialization` 至少携带 continuous-state 与 event-indicator 数量、初始 next time event 和当前状态/nominal 是否发生变化；`DiscreteStateUpdate` 至少表达是否仍需迭代、terminate 请求、state/nominal changed，以及 next time event。FMPy adapter 负责把 FMI2 `fmi2EventInfo` 转换为这些 DTO。

solver 的 callback/problem 边界由 application 使用上述 session 组装：在每次导数或 root 评估前设置 time/连续状态，并从 session 取得导数或 event indicator。具体 callback ABI、NumPy view 和 ctypes buffer 仅由 `FmpyCvodeSolverAdapter` 持有。

## 7. SolverAdapter port

3.0B 已新增 Farcel-owned `SolverAdapter`、`SolverFactory` 和 `ModelExchangeProblem` ports。它们的输入是 ME problem/session 的 Farcel callback，输出是 Farcel dataclass；不出现 `fmpy.sundials.CVodeSolver`、NumPy array 或 native handle。

```python
class SolverAdapter(Protocol):
    def initialize(self, problem: ModelExchangeProblem, options: SolverOptions) -> None: ...
    def integrate_to(self, target_time: float) -> SolverAdvanceResult: ...
    def reset(self, time: float, reason: SolverResetReason) -> None: ...
    def close(self) -> None: ...
```

`SolverAdvanceResult` 至少包含 `reached_time`、`status`（到达 target、state event、失败）、`root_info`（Farcel 标准整数 tuple）和可诊断的失败信息。application 根据失败信息创建稳定 `EngineError`；底层异常文本只放 `details["diagnostic"]`。`SolverOptions` 以标准数值表达 relative tolerance、可能的 maximum step/step limit，不暴露 CVode 的常量、算法枚举或内存对象。

CVode、RK45 与未来 C/C++ 实现都必须满足同一 port。`reset()` 是显式可测试操作，不是隐含在 `integrate_to()` 的副作用。

## 8. FMI 2.0 ME 初始化和数据流

FMI2 ME 的计划生命周期为：

1. infrastructure 解压、按 ME model identifier 实例化 `FMU2Model`，并持有 instance 与临时目录。
2. runner 在 `RunControl` 未预先 stop 时，调用 `setupExperiment`；按 config 在 Initialization Mode 允许的时机写参数、initial inputs 与 tolerance。
3. 进入并退出 Initialization Mode；执行 `newDiscreteStates()` 直到 FMU 宣告稳定或达到 Farcel 的有限迭代保护上限。
4. 处理初始 terminate/next time event；进入 Continuous-Time Mode；读取初始 canonical sample 并初始化 solver。
5. solver 的 RHS 回调执行 `set_time(t)`、`set_continuous_states(x)`、`get_derivatives()`；root callback 同样设置当前 time/state 后读取 `get_event_indicators()`。
6. solver 返回后，runner 将 reached time/state 同步到 FMU，处理 input/time/state event 与 `completedIntegratorStep()`，必要时进入 Event Mode、离散迭代并回到 Continuous-Time Mode。

参数、input/output value reference 映射和 FMPy getter/setter 始终留在 FMPy ME adapter；runner 只观察 session port 的标准值。

## 9. Event Mode、输入与 `completedIntegratorStep()`

下一推进 target 是当前 checkpoint、下一个合法 input schedule 时间、FMU 报告的 next time event 与 stop time 中最早的边界。state event 由 solver root 返回；input event 由 `input_schedule` 驱动；time event 来自最后一次稳定的离散更新。

在到达事件时，runner：同步 time/state、应用事件时刻应生效的输入、调用 `completedIntegratorStep()`（若 capability 要求），并在 input/time/state/integrator event 任一成立时进入 Event Mode。随后反复执行离散状态更新，处理 terminate 请求与下一 time event，最后返回 Continuous-Time Mode。每个离散迭代必须有上限和稳定 `STEP_ERROR` 映射，避免坏 FMU 无限循环。

`completedIntegratorStep()` 是 ME solver 与 FMU 的协作点，不是 Farcel checkpoint 的完成信号。它提出 event/terminate 时必须先走事件生命周期；它既不自动增加 `completed_steps`，也不直接发送 `ResultChunk`。

## 10. Solver reset 规则与 FMPy 0.3.31 风险

CVode 是第一候选，因为 FMPy 0.3.31 已随依赖分发 SUNDIALS/CVode，并提供 BDF、event root、连续状态/导数/nominal callback 和 reached-time 能力。它仍是 infrastructure choice，不是公共 contract 的承诺。

本地锁定的 FMPy 0.3.31 源码显示：`simulateME()` 在所有 input/time/state/integrator event 的处理后无条件执行 `solver.reset(time)`；其 `CVodeSolver` 直接使用 NumPy/ctypes，且只有 `__del__()` 调用 `CVodeFree`。这既不适合 Farcel 的可控生命周期，也会把 reset 条件藏在第三方高层循环中。

[FMPy Issue #882](https://github.com/CATIA-Systems/FMPy/issues/882) 记录了 0.3.30 起的 `CV_TOO_CLOSE` 回归：纯 time event 未改变连续状态时，无条件 `solver.reset(time)` 会使下一次 CVode 推进在 roundoff 范围内失败。该问题在本地 0.3.31 的上述无条件调用形态仍须作为 release blocker 验证，不能假定依赖版本号已经消除风险。

因此 Phase 3 的强制规则是：

- 不把 FMPy `simulateME()` 当 Farcel ME runtime。
- reset 由 `ModelExchangeRunner` 依据离散更新返回的 continuous-state/nominal 改变、solver problem 是否实际失效和当前时间关系作出明确决定；不得仅因进入事件分支或纯 time event 就无条件 reset。
- 每次 reset 必须记录 `SolverResetReason`，并由包含纯 time event 的回归 FMU 验证不会出现 `CV_TOO_CLOSE`。
- solver adapter 必须提供确定性的 `close()`；不依赖 Python 析构时机。当前 FMPy 若没有公共 close API，适配器在 infrastructure 内实现受控释放或以稳定错误报告该限制，绝不泄漏 handle。

## 11. Stop、Progress、ResultChunk 与 cleanup

ME 复用现有 `RunControl`、`RunProgress`、`SimulationResult`、`ResultChunk` 和 `_ResultAccumulator`（或行为完全等价的 Farcel-owned 重构）。不新增第二套 UI callback，也不把 solver 内部步骤/事件当作普通 output sample。

Stop 保持 cooperative：runner 在每个 Farcel checkpoint 前和每次 solver 返回后检查 stop；已进入 native CVode 的一次 `integrate_to()` 不能被 Farcel 强制中断。初始化后 stop 返回可导出的 `STOPPED` partial canonical result，必要时补记实际 final point；预启动 stop 仍为 `CANCELLED`。

进度的 `current_time` 使用 solver 实际 reached time，`completed_steps` 只统计完整 checkpoint。progress 与 chunk callback 都在 `run_fmu()` 调用线程执行；callback 异常仍映射为 `INTERNAL_ERROR` 并经过同一 cleanup。

cleanup 顺序固定为：终止 FMU（状态允许时）→ close solver → free FMU instance/native library → 删除解压目录。主运行错误优先，cleanup error 作为稳定 `details` 附加；每个组件 close 必须幂等，并且 runtime 失败不能伪造 terminal `ResultChunk`。

## 12. 回归计划

3.0B 已用 characterization tests 固定现有 CS 的 selected output、output interval、Early Return、Stop、RunProgress、chunk sequence/final chunk、cleanup 与旧 `SimulationConfig` 位置参数语义；新增双接口默认 CS、显式 CS 结果一致和显式 ME 早期拒绝测试。后续重构不得改变这些测试保护的行为。

ME 回归矩阵至少包含：

| 用例 | 目的 |
|---|---|
| 有连续状态、无 event 的 FMI2 ME reference FMU | 初始化、导数推进、数值结果与时间轴 |
| 有 state/root event 的 FMI2 ME FMU | root info、Event Mode、离散迭代和继续推进 |
| 有纯 time event 的 `SampleCount` 类 FMI2 ME FMU | Issue #882 / `CV_TOO_CLOSE` 防回归；确认不作无条件 reset |
| 有 input schedule 的 FMI2 ME FMU | input checkpoint 边界、保持值和事件顺序 |
| `needsCompletedIntegratorStep` 的 FMI2 ME FMU | step-event、terminate 和 Event Mode 次序 |
| 取消、callback 异常、solver/FMU 失败 | partial result、stable error、native/临时资源 cleanup |

真实 FMU 应优先使用未改动的、可在 Windows win64 运行的 FMI2 ME Reference FMU；本地生成或 patched fixture 必须标注构建方式、来源、许可证和 SHA。每次 FMPy 升级都重跑完整 CS + ME corpus，特别包括纯 time event 用例。

## 13. 路线图

| 阶段 | 交付 |
|---|---|
| 3.0A（本次） | 本设计、风险和语义冻结；无 runtime 改动 |
| 3.0B（已完成） | CS characterization tests；增加 additive `execution_interface`、ME/solver DTO 与 ports；`phase-3-work` push 纳入完整后端 CI；仍不接入正式 runtime |
| 3.1（已完成） | application runner 抽取：`FarcelEngine` 保留加载、validation 和接口分派；`CoSimulationRunner` 持有既有 CS loop；建立可注入 `ModelExchangeRunner` 骨架，ME validation 仍在 native runtime 前拒绝 |
| 3.2（已完成） | FMI2 `ModelExchangeSession` adapter、初始化离散状态迭代、continuous-time primitive 与 application problem boundary；仍不接入 solver 或 public ME runtime |
| 3.2.1（已完成） | 在解压和 `FMU2Model` 构造前，按 FMPy 0.3.31 最终运行时路径校验当前平台的 ME `modelIdentifier` native library；缺失时稳定为 `PLATFORM_BINARY_MISSING`，不把 CS binary 误当作 ME binary |
| 3.3（已完成） | FMPy 低层 SUNDIALS 7 binding 上的 CVode adapter/factory、deterministic close、无事件 ME checkpoint 推进与 VanDerPol 数值基线；public ME runtime 仍关闭 |
| 3.4（已完成） | application 内部 FMI2 ME state/time/input event coordinator、capability-gated `completedIntegratorStep()`、有界离散迭代与条件 reset；纯 time/no-change event 不 reset，public ME runtime 仍关闭 |
| 3.5 | Stop/Progress/ResultChunk/cleanup/error 端到端强化 |
| 3.6 | Reference FMU 兼容性矩阵、Issue #882 防回归与性能/泄漏检查 |
| 3.7 | 公共 API/CLI/前端集成文档、稳定性审查；FMI3 ME 仍须另立范围 |

Phase 3 的 exit 是完成上述 3.3–3.7 的 FMI2 ME solver/runtime 路径并保持 CS 回归稳定；之后才进入 Phase 4 的本地 `SimulationGraph`、`SimulationOrchestrator`、scheduler、data routing 与 multi-FMU node composition。FMI3 Model Exchange 与 Scheduled Execution 都不构成 Phase 3 gate，必须另行定义范围。

## 14. 前端成果进入 main 时的同步流程

3.0A 开始时未发现 frontend branch、frontend PR 或开放 PR；未来出现前端成果并合入 `main` 时，不随意改变 Phase 3 基线。只在稳定里程碑插入 `Phase 3.SYNC`：确认 `phase-3-work` clean → `git fetch origin` → 检查 `origin/main` 提交来源 → 在 `phase-3-work` **merge** `origin/main`（不 rebase、不 force）→ 逐文件处理冲突且不覆盖前端成果 → 执行完整后端 tests、frontend/backend integration smoke、`examples/backend_api_example.py`、`git diff --check` 和 public API compatibility 检查 → 再继续 Phase 3。

在完成前述验证前，不把前端同步解释为 ME runtime 已实现，也不修改 GUI 去绕过 `create_backend()` 或 public contracts。

## 15. 3.0B 已交付边界

- `SimulationConfig.execution_interface` 是末尾 additive 字段；旧 GUI 不设置它时，双接口 FMU 继续选择 CS。
- `ModelExchangeInitialization`、`IntegratorStepResult`、`DiscreteStateUpdate`、`SolverAdvanceResult`、`SolverOptions` 与相关 enum/ports 已从 `farcel.contracts` 公开；它们是 contract，不是 runtime 实现。
- CS 的 Early Return、Stop、Progress、ResultChunk、cleanup 与 sampling grid 由现有和新增 characterization tests 保护。
- GitHub Actions 已把 `phase-3-work` push 纳入与 `main` 相同的后端 CI matrix。
- `FarcelEngine` 未注入 ME 依赖，未实例化 `FMU2Model`，也未调用 CVode 或 FMPy `simulateME()`。

## 16. 3.1 已交付边界

- `FarcelEngine.run_fmu()` 保持公开调用形态，执行前仍完成 result chunk 参数检查、预启动取消检查、metadata 加载、配置 validation 与 interface dispatch。
- `CoSimulationRunner` 位于 application 层，只依赖 Farcel `SessionFactory` / `SimulationSession` port 与 Farcel DTO/error；原有初始采样、communication target、Early Return、scheduled input、Stop、progress、ResultChunk、terminate/close cleanup 语义保留在该 runner。
- `ModelExchangeRunner` 仅是 application 内部可注入分派占位；显式 ME 继续由 validation 以稳定 `CONFIG_ERROR` / `UNSUPPORTED_INTERFACE` 在 runner 与 native session factory 前拒绝。
- 本阶段没有 FMI2 ME adapter、`FMU2Model`、CVode/SUNDIALS、`simulateME()`、SciPy 或 FMI3 ME runtime 改动。

## 17. 3.2 已交付边界

- `FmpyFmi2ModelExchangeSessionFactory` 只直接创建具备当前平台二进制的 FMI2 Model Exchange session；它不改变 importer 的 `can_execute` 或 public execution policy。
- `FmpyFmi2ModelExchangeSession` 在 infrastructure 内封装 `FMU2Model`、native instance 与临时目录，完成 setupExperiment、参数/initial input、Initialization Mode、有限初始 `newDiscreteStates()` 迭代和 Continuous-Time Mode 进入。
- adapter 通过现有 Farcel DTO/port 暴露 time、continuous states、derivatives、event indicators、completedIntegratorStep、Event Mode primitive、selected outputs、terminate 与幂等 close；application 的 `SessionModelExchangeProblem` 只委托这些标准值。
- 没有 numerical solver、时间推进循环、ME `RunControl`/Progress/ResultChunk、CVode 或 FMI3 ME；显式 `run_fmu(..., execution_interface=MODEL_EXCHANGE)` 仍在 validation 阶段稳定拒绝。

## 18. 3.2.1 已交付边界

- `metadata.platforms` 仅表示容器中有某个可识别平台库；FMPy 0.3.31 的 `supported_platforms()` 可以识别 legacy 与 platform-tuple directory，但 `FMU2Model` 的最终运行时 resolver 只加载 `binaries/<current_platform>/<ME modelIdentifier><sharedLibraryExtension>`。
- factory 在创建临时目录、解压和 native `FMU2Model` 构造前检查该精确 archive member。CS model identifier 的库不能满足 ME interface 的要求；缺失结果稳定映射为 `PLATFORM_BINARY_MISSING`，details 含 platform、model identifier 与 expected archive path。
- 此修复不改变 importer metadata/executable policy；真实 `VanDerPol.fmu` ME session primitive 继续回归。显式 public `run_fmu(..., execution_interface=MODEL_EXCHANGE)` 仍由 validation 在 runner/session factory 前以 `CONFIG_ERROR` / `UNSUPPORTED_INTERFACE` 拒绝。
- 3.2.1 不是 CVode、数值积分、ME runner、FMI3 ME 或公开 ME simulation 的实现。项目的异构数字模型集成定位、Phase 4 graph 和更远期分布式/实时方向仅记录在项目路线图中，不能被解读为现有能力。
