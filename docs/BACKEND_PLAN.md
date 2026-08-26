# 后端开发者项目规划报告

**目标与边界。** 后端是 Farcel 的“可信业务核心”：拥有 FMU 解析、FMI/FMPy Adapter、Session 生命周期、参数和仿真条件验证、执行、结果生成、CSV、CLI、日志、错误规范化及测试基准。GUI 和 CLI 均不得绕开这一层。

当前 FMPy `simulate_fmu()` 已暴露 start/stop time、step size、relative tolerance、output interval、start values、inputs、selected outputs、logging、step callback，以及 FMI 3 Co-Simulation 的 `early_return_allowed` 和 `use_event_mode` 等能力；Farcel 应在其上建立更稳定、更小的项目级接口，而不是把这个函数签名直接作为前后端契约。citeturn17view0 FMPy 也提供独立 `instantiate_fmu()` 路径，并能根据 model description 选择 FMI 2/3 Co-Simulation、Model Exchange 等对象，这使逐步执行和未来扩展具备可行基础。citeturn17view1

**核心责任**

| 后端服务 | 范围 |
|---|---|
| FMU Importer | 打开 `.fmu`、验证容器、读取 `modelDescription.xml` |
| Metadata Normaliser | 把 FMI 2/FMI 3 差异转换为 Farcel DTO |
| Capability Detector | FMI version、CS/ME/SE、platform、event/early-return 等能力 |
| Configuration Validator | 参数类型、范围、时间范围、step/output interval 合法性 |
| Simulation Session | load / initialise / start / step / stop / terminate / cleanup |
| Result Recorder | 选定变量、采样时间、结果 chunk |
| Export Service | UTF-8 CSV |
| CLI | inspect / validate / run / export |
| Diagnostics | Engine/FMI/FMPy 日志、错误分类、运行标识 |
| Adapter | 将 FMPy 隔离在单一边界内 |

FMPy 的 `read_model_description()` 当前可以不解压整个 FMU 而直接读取 ZIP 中的 `modelDescription.xml`，并可进行 FMI schema validation。因此 metadata import 应优先完成解析后再决定是否具备当前平台可执行 binary，而不能将“模型能读”与“模型能跑”混成一个状态。citeturn17view2

**FMU 元数据至少提取**

`fmi_version`、`interface_types`、`model_name`、`description`、`guid/instantiation_token`、`model_identifier`、`generation_tool`、`generation_time`、`platforms`、`default_experiment`、capability flags、variable count，以及每个变量的：

`name`、`value_reference`、`data_type`、`causality`、`variability`、`initial`、`start`、`min`、`max`、`unit`、`display_unit`、`description`、`declared_type`、`shape`。

FMI 3.0 规范明确把变量定义置于 `ModelVariables`，以 unique value reference 标识；`causality` 表示相对于 FMU 的信息流方向。citeturn1search1 DefaultExperiment 可提供 start/stop/tolerance 等默认条件，而 FMPy 当前也将这些字段解析进 model description。citeturn9view2turn17view2

**CLI 范围建议**

```text
farcel inspect  model.fmu [--json]
farcel validate model.fmu
farcel run      model.fmu --config run.farcel.json
farcel export   <result-or-run-id> --csv result.csv
```

CLI 必须使用和 GUI 完全相同的 `SimulationConfig`、engine 和 validation，不建立第二套执行代码。

**日志和错误分类。** 至少区分 `IMPORT_ERROR`、`VALIDATION_ERROR`、`UNSUPPORTED_FMI`、`UNSUPPORTED_INTERFACE`、`PLATFORM_BINARY_MISSING`、`CONFIG_ERROR`、`INITIALIZATION_ERROR`、`FMI_RUNTIME_ERROR`、`TIMEOUT/CANCELLED`、`EXPORT_ERROR` 与 `INTERNAL_ERROR`。错误对 UI 提供稳定 code + message + details；底层 FMPy exception 文本只放 diagnostics，不作为 UI contract。

**非功能要求。** 相同 FMU、配置、FMPy 版本及平台应产生可重现结果；任何失败路径必须释放 FMU/session 资源；停止请求应具有有界响应而不能让 GUI 永久等待；大量输出变量时结果传递应支持分块；后端必须能脱离 GUI 运行测试和 CLI。

FMPy 在 2026 年仍持续发布修复，例如 0.3.30/0.3.31 紧邻本报告日期发布，最近版本持续包含 FMI、变量、solver 等调整，因此项目必须锁定依赖并通过 Reference FMU 回归后才能升级。citeturn5search0turn5search1

**阶段交付物。** MVP：Importer、Metadata、FMI2/3 CS Session、CLI、CSV、日志与接口；Beta：FMI3 Event Mode/Early Return、step/stop 稳定性、数组和错误路径、独立验证；v1.0：Reference FMU 回归矩阵、API/CLI 文档、性能与资源清理测试、C/C++/ME/multi-FMU 扩展设计说明。

**迁移考虑。** FMI 规范 API 本身以 C 定义，因此未来 C/C++ engine 可以保留当前 DTO 和 session contract，仅替换内部 adapter。citeturn2view1 Model Exchange 扩展应增加 `SolverAdapter` 而不是修改 GUI contract，因为 FMI ME 明确要求 importer 负责时间推进、continuous states、derivatives 和事件处理。citeturn2view1turn8view1 Multi-FMU 则增加 orchestrator/master 层；FMI 3.0 规范本身已经展示 connected FMUs 通过读取输出、设置输入并分别 `doStep` 的基本模式。citeturn8view2
