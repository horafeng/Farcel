# Farcel 后端架构与演进边界

## 1. 项目目标与后端职责

Farcel 的目标是提供本地优先的桌面 GUI 与 CLI，并让两者复用同一套 FMU 业务语义。第一阶段只打通单 FMU 的 FMI 2.0 / FMI 3.0 Co-Simulation 工作流；Model Exchange 与 Scheduled Execution 只识别元数据，不执行。

后端是可信业务核心，负责导入与元数据规范化、能力检测、配置验证、会话生命周期、结果分块、CSV 导出、诊断和 CLI。GUI 只消费 Farcel DTO 和引擎接口，不解析 FMU、不调用 FMPy、不维护另一套默认值或状态机。

必须区分两个判断：FMU 能否被解析，以及它能否在当前平台执行。解析成功不等于存在兼容二进制，也不等于 Farcel 支持其接口类型。

## 2. 建议目录结构

```text
src/farcel/
  contracts/          # 稳定、可序列化、与实现无关的 DTO、枚举、错误、端口
  application/        # 用例编排：导入、验证，后续加入 session facade
  infrastructure/
    fmpy/             # 唯一允许依赖/import FMPy 的位置
    export/           # CSV 等外部格式适配器（MVP 后续补齐）
  cli.py              # 薄入口，只调用 application
tests/
  unit/               # 不依赖真实 FMU/FMPy
  integration/        # 后续放 Reference FMU 回归
docs/
```

暂不拆成多个可发布包，不引入依赖注入框架、消息总线、数据库或 worker 进程。

## 3. 模块边界

- `contracts`：公共边界，只能使用 Python 标准类型、Farcel 枚举和不可变 dataclass。禁止出现 `fmpy.*`、FMI C handle、NumPy 专有数组类型或 PyQt 类型。
- `application`：实现用例和规则，依赖 `contracts` 中的端口，不直接依赖 FMPy。默认实验值合并、配置合法性和状态转换都归这里。
- `infrastructure.fmpy`：把 FMPy 模型、异常、状态和数据转换为 Farcel DTO。底层异常原文只能进入 diagnostics；对外抛稳定的 `EngineError`。
- `infrastructure.export`：只消费 canonical result，不重新运行仿真，也不建立第二份结果语义。
- `cli`：参数解析和展示；不含 FMI/FMPy 业务逻辑。GUI 将与它处于同一层级。

依赖方向固定为：`CLI/GUI -> application -> contracts <- infrastructure`。组合根负责把具体 adapter 注入 application。

公开组合根为 `farcel.create_backend()`。CLI 与未来 GUI 均通过它获得完整配置的 application facade；消费者不自行导入或组装 infrastructure adapter。具体前端集成契约见 `docs/FRONTEND_BACKEND_INTEGRATION.md`。

## 4. 第一阶段 MVP 开发顺序

1. 冻结最小 DTO、错误码、端口和 JSON 表达；用架构测试禁止 FMPy 类型越界。
2. 实现 FMU 容器检查、FMPy metadata adapter 与规范化映射；先做到 `inspect`，明确 parsed/executable 状态。
3. 实现 `SimulationConfig` 默认值解析与验证；GUI、CLI 共用同一 validator。
4. 实现 FMI 2.0 Co-Simulation 的最小 session 生命周期与资源清理，再用 Reference FMU 做数值基线。
5. 在同一 session contract 下接入 FMI 3.0 Co-Simulation；`StepResult` 保留 reached time、event、early return 和 termination。
6. 实现 result chunk、选择性记录与 CSV；CSV 和 GUI 必须消费同一 canonical result。
7. 补齐薄 CLI 的 `inspect / validate / run / export`，随后再让 GUI 集成。
8. 最后强化 stop/step 错误路径、日志、集成回归与打包。

每一步先通过无 GUI 自动化测试，再进入下一步。

## 5. 现在必须确定的设计

- 公共 DTO、错误码和 session 状态的语义，以及配置/结果的 schema version。
- FMPy 只能存在于 infrastructure adapter；公共接口不泄漏其类型。
- v1 可执行范围是单 FMU FMI 2/3 Co-Simulation；ME/SE 仅识别。
- “可解析”与“可执行”分离；参数默认值只由后端解析。
- `StepResult` 必须表达实际到达时间、事件、early return、终止请求。
- 结果以选定变量分块传递，CSV 来自 canonical result。
- 资源清理、取消和错误转换是 session 生命周期的一部分。

## 6. 应推迟的设计

- Model Exchange solver、Scheduled Execution scheduler、多 FMU orchestrator。
- 独立 worker 进程、远程 RPC、插件系统、数据库和复杂缓存。
- FMI 3 全量数组绘图策略、Binary/Clock 可视化、超大结果的持久化格式。
- C/C++ 重写细节；当前只保证语言无关契约可映射。
- 高级事件总线、可恢复运行、分布式执行和性能预优化。

这些能力出现真实需求或测试证据后再设计，避免当前骨架预设错误抽象。

## 7. 当前实现范围

当前已打通真实 FMU → FMPy → Farcel `ModelMetadata` → CLI `inspect` 链路。元数据导入支持 FMI 2.0 / 3.0，并识别 Co-Simulation、Model Exchange 和 Scheduled Execution；只有当前平台具备二进制且不要求外部执行工具的 Co-Simulation 才标记为当前可执行。ME/SE 只识别，不执行。

`SimulationConfig` 的仿真前验证也已可用，并通过 CLI `validate` 暴露。它验证时间范围、communication step、当前执行策略、参数名称/因果性/基础标量类型/范围，以及输出变量名称。无效报告由 application facade 转换为稳定的 `CONFIG_ERROR`，CLI 不包含核心验证规则。

FMI 2.0 与 FMI 3.0 Co-Simulation 的 Session 生命周期均已实现：application 通过同一组 Farcel `SessionFactory` / `SimulationSession` 端口完成 instantiate、initialize、parameter override、doStep、terminate 和 close；通用 FMPy factory 只在 infrastructure 内按 metadata 版本选择 adapter。FMPy instance、native library 与临时解压目录始终由对应 infrastructure session 持有。Phase 3.2 额外提供仅供未来 solver 使用的 FMI2 `ModelExchangeSession` adapter 和 application problem primitive；它不改变 public `run_fmu()` 的 CS-only 执行策略，也不包含数值积分循环。

`SimulationResult` 现已作为 implementation-independent canonical result：application 在初始化完成后采集初始 communication point，并仅在由 `output_interval` 指定的实际到达 communication point 采集配置选中的输出。`communication_step` 始终控制 FMU 推进；未显式设置的 `output_interval` 回退为该步长，正常完成时会补记尚未采样的最终状态。FMPy getter 与 value reference 映射仅存在于 infrastructure adapter；CLI `run` 只展示 application 返回的结果摘要及首尾样本。

Phase 2.0B 在 contracts 中提供 `RunControl` 和 `RunProgress`，由 application 的 Co-Simulation runner 实现 cooperative stop 与进度通知；`FarcelEngine.run_fmu()` 保持加载、validation 和执行接口分派的公开 facade。`RunControl` 使用标准库同步原语，仅表达“在下一个 communication point 停止”；FMPy adapter 不认识它，也不会尝试中断正在执行的 native `doStep()`。初始化后发生的停止会正常 terminate / close 并返回 `SimulationState.STOPPED` 的 canonical partial result，末尾补记实际 final state；该结果仍可由既有 CSV adapter 导出。progress callback 只传递 DTO，且在 run 调用线程执行；其异常被转换为稳定 `INTERNAL_ERROR`，再复用既有 cleanup 路径。Model Exchange runner 目前只保留 application 内部分派占位，显式 ME 仍在 validation 阶段拒绝，未进入 native runtime。

Phase 2.0C 在同一 application 采样路径上增加 Farcel-owned `ResultChunk` 回调：累积器只接收 canonical result sample，绝不把 communication step 或 FMPy 对象暴露给调用者，也不会触发额外 `read_outputs()`。每次 `run_fmu()` 的可观测流使用一个新的 UUID `run_id` 和从零连续递增的 `sequence`；满块只在下一样本到来前以非终块送出，完成或 `STOPPED` 时将最后一个非空块标记为唯一 `final_chunk=True`。完整 `SimulationResult` 仍由 application 保留并返回，因此此功能不是 bounded-memory execution mode。chunk callback 同样在调用线程运行，异常映射为独立的 `INTERNAL_ERROR` 诊断并经过既有 cleanup；运行时 FMU 错误不会伪造 final chunk。

CSV 导出通过 Farcel `ResultExporter` 端口消费已经完成的 `SimulationResult`。标准库 CSV adapter 位于 `infrastructure/export`，不依赖 FMPy、不重新执行 FMU，也不重建时间轴；CLI `export` 复用 application 的 `run_fmu` 后再委托 exporter。

Phase 2.1 的 FMI 3 adapter 仅在 Co-Simulation capability 声明支持时，以 `eventModeUsed=True` / `earlyReturnAllowed=True` 实例化。Event Mode 在 exitInitializationMode 后和 doStep 报告 event 后执行 `updateDiscreteStates()` 直至稳定，再进入 Step Mode；1000 次迭代上限防止坏 FMU 挂死。合法 Early Return 保留在 Farcel `StepResult`，application 以实际 reached time 继续请求原配置 communication target，只有完整到达该 target 才增加 `completed_steps` 和执行常规采样。Intermediate Update 数据回调、Clock/Binary、Scheduled Execution 与 Model Exchange runtime 仍未实现。

Phase 2.2A 为 FMI 3 Co-Simulation 的 metadata 已解析、默认尺寸数组提供完整数据链路：validation 对 public nested sequence 严格匹配 `VariableMetadata.shape`，adapter 在 Initialization Mode 内 flatten 写入并按 getter 的 `nValues=product(shape)` reshape 为 nested tuple。数组参数、initial/scheduled input、selected output、canonical `SimulationResult`、`ResultChunk` 和 CSV 都使用同一数组语义；CSV 仅在导出边界展开为零基 indexed columns。此能力不引入新 DTO、NumPy 或 FMPy 对象到 public contracts，也不改变标量路径。

Phase 2.2B 支持 FMI 3 Co-Simulation 的标量整型/枚举型 `structuralParameter` 覆盖：validator 先以覆盖后的结构参数解析 dimension value reference，adapter 仅在存在这类覆盖时进入并退出 Configuration Mode，随后在 Initialization Mode 写入普通参数和输入。有效 shape 只保存在单次 validation/session 运行内；导入元数据的默认 `shape` 不会被修改。数组结构参数、Reconfiguration Mode、运行中结构参数改变、Binary/Clock、Intermediate Update 数据回调、Scheduled Execution、worker、多 FMU 和 ME solver 尚未实现。

Phase 2.3 使用官方 Reference FMU 扩展真实兼容性回归，而不增加运行能力：Feedthrough 覆盖当前 FMI 3 scalar setter/getter 与 ResultChunk/CSV 路径，Resource 覆盖已解压 FMU 的 `resources/` 访问和关闭清理，Clocks 则验证 Scheduled Execution/Clock 能被 inspect 且在 session 创建前由执行策略拒绝。FMI 3 Binary 与 Clock 不进入 adapter runtime；validation 对 Binary/Clock selected output 返回稳定的 `UNSUPPORTED_OUTPUT_TYPE`，对 Binary input 保持 `UNSUPPORTED_INPUT_TYPE`。

## 8. Phase 4 以后：规划的本地多模型边界

以下结构是 **Planned**，目前没有 `SimulationGraph`、scheduler、data router、多 FMU runtime 或 Simulink/AMESim/ANSYS adapter：

```text
SimulationOrchestrator
  ├─ SimulationGraph
  ├─ Scheduler
  ├─ DataRouter
  └─ ModelNodeAdapter
       ├─ FMU node runtime（复用现有单 FMU engine）
       └─ future direct adapters: Simulink / AMESim / ANSYS / ...
```

单 FMU engine 不会被丢弃；它将成为 FMU node runtime，由未来的 graph/orchestrator 组合。该演进仍保持 `GUI / CLI → application → contracts ← infrastructure`：图、调度和数据路由的业务语义属于 application/contracts，工具/API/native 细节继续留在 infrastructure。分布式、实时/HIL、ROM 和直接异构工具连接都必须等待本地 `SimulationGraph` 得到验证后再单独立项。
