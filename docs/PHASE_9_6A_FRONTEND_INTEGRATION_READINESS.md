# Phase 9.6A：前后端集成准备与公共工作流验收

## 1. 范围、结论与阅读顺序

本文是 PySide6 前端接入当前 Backend 的准备记录，不是 GUI 已完成或已联调的声明。
Phase 9.6A 没有修改 GUI 生产代码、FMU Runtime、Project schema 或公开 Backend API；也没有
合并旧的 `feat/frontend` 分支。

前端负责人应按以下顺序阅读：

1. 本文；
2. `FRONTEND_BACKEND_INTEGRATION.md` 第 17、19–21 节；
3. `PHASE_9_1D_MODELICA_PUBLIC_API.md`、`PHASE_9_2D_MODELICA_PROJECT_PUBLIC_API.md`；
4. `PHASE_9_4A_MODELICA_PARAMETER_BOUNDARY.md`、`PHASE_9_4B_PARAMETER_EDITOR_CONTRACT.md`；
5. `PHASE_9_5A_MODELICA_COMPATIBILITY_MATRIX.md`、`PHASE_9_5B_OPTIONAL_OPENMODELICA_CI.md`；
6. `tests/integration/test_modelica_frontend_public_workflow.py`，作为不依赖 PySide6 的可执行消费者示例。

前端只导入 `create_backend()`、`farcel.contracts` 和 `EngineError`。不得导入
`farcel.infrastructure` 或 FMPy，不得直接调用 OMC、解析 Modelica 源码或 OMC stderr，且不得
直接读写 `project.json` 或结果 JSON。

状态定义：`COMPATIBLE` 仅表示代码审查或公开测试证明该边界可用；`ADAPTATION_REQUIRED`
表示可在公开 API 内完成但旧界面必须修改；`NOT_IMPLEMENTED` 表示旧 GUI 没有该功能；
`NOT_VERIFIED` 表示尚未实际运行 GUI；`BLOCKED` 只用于存在无法绕过的外部条件。本阶段没有
确认的 Backend `BLOCKED` 项。

## 2. 已实现的后端能力

| 能力 | 公共入口 | 状态 | 前端调用要点 |
|---|---|---|---|
| OMC 环境检测 | `get_modelica_environment()` | PASS | 显示 `AVAILABLE`、`NOT_FOUND`、`UNUSABLE` 及稳定诊断。 |
| 独立 Modelica 编译 | `compile_modelica()` | PASS | `.mo`、模型类名和目标目录由用户选择；仅支持当前公开的 FMI 2.0 CS 编译范围。 |
| Project 显式迁移 | `open_project()`、`migrate_project_schema()` | PASS | 1.0 不会自动升级；必须先确认再迁移。 |
| Project 编译登记 | `compile_project_modelica()` | PASS | 仅 1.1 Project；成功返回更新后的 Project 和已登记构建。 |
| 来源展示 | `list_project_modelica_builds()` | PASS | 纯 DTO 读取，不做 I/O、编译或 OMC 探测。 |
| FMU 加载、校验、运行、导出 | `load_fmu()`、`validate_config()`、`run_fmu()`、`export_result()` | PASS | 普通 FMU 仿真不需要安装 OMC。 |
| 参数、初始输入和时序输入 | `SimulationConfig`、`validate_config()` | PASS | 使用同一个后端校验器；不要在 GUI 复制规则。 |

## 3. 公共方法审计与建议调用顺序

所有方法均为同步调用；前端必须在工作线程调用可能进行文件 I/O、编译、加载或运行的方法，
再把不可变 DTO 或 `EngineError` 回送 GUI 主线程。Backend 不负责创建 Qt 线程，也未声明可由
多个 GUI 线程同时安全调用。

| 方法 | 输入 / 返回 | I/O、OMC、Project 影响 | 错误与线程建议 |
|---|---|---|---|
| `get_modelica_environment(compiler_path=None)` | 可选本机路径 → `ModelicaEnvironmentInfo` | 启动/探测指定或发现的 OMC；不改 Project | 可能阻塞；环境不可用通过状态返回，显示 `diagnostic_message`。 |
| `compile_modelica(ModelicaCompileRequest)` | 源文件、类名、输出目录、可选 OMC、FMI/类型/超时 → `ModelicaCompileResult` | 读源、写目标、调用 OMC；不改 Project | 工作线程；`CONFIG_ERROR`、`MODELICA_COMPILER_NOT_FOUND`、`MODELICA_COMPILER_UNUSABLE`、编译/输出错误均为 `EngineError`。 |
| `open_project(project_root)` | 根目录 → `SimulationProject` | 读 `project.json`；不迁移、不编译、不改 Project | 工作线程；`PROJECT_IO_ERROR` 或 `PROJECT_FORMAT_ERROR`。 |
| `migrate_project_schema(project_root)` | 根目录 → `ProjectSchemaMigrationResult` | 读写 `project.json` 与可恢复备份；将 1.0 显式变为 1.1 | 必须得到用户确认；工作线程；失败保持稳定 Project 错误码。 |
| `compile_project_modelica(project, ModelicaAssetStagingRequest)` | 已打开 1.1 Project 与 staging 请求 → `ProjectModelicaCompilation` | 调用 OMC、暂存/发布 FMU、登记 Project | 工作线程；成功后以返回的 `registration.project` 整体替换 GUI 状态。 |
| `list_project_modelica_builds(project)` | 已打开 Project → `tuple[ModelicaBuildProvenance, ...]` | 无 I/O、无 OMC、无 Project 修改 | 可直接做展示；用 `fmu_asset_id` 在 `project.model_assets` 中关联 `ModelAsset`。 |
| `load_fmu(path)` | FMU 路径 → `ModelMetadata` | 读 FMU；仅更新 Backend 实例的已加载模型缓存 | 工作线程；不需要 OMC；失败为稳定导入/能力错误。 |
| `validate_config(metadata, config)` | `ModelMetadata`、`SimulationConfig` → `ValidationReport` | 无磁盘 I/O、无 OMC、无 Project 修改 | 可在编辑后调用；非法时抛 `CONFIG_ERROR`，`details["issues"]` 含 `field`、`code`、`message`。 |
| `run_fmu(path, config, *, control, on_progress, on_result_chunk, result_chunk_size)` | FMU、已校验配置与可选回调 → `SimulationResult` | 读/解包并运行 FMU；不改 Project | 工作线程；回调在调用线程执行，Qt 必须自行 marshal；普通 FMU 不需要 OMC。 |
| `export_result(result, destination)` | `SimulationResult`、CSV 路径 → `ExportReport` | 写 CSV；不需要 OMC、不改 Project | 工作线程；`EXPORT_ERROR` 等稳定错误供界面显示。 |

推荐单 FMU 调用顺序是：`load_fmu` → 根据 metadata 构造 `SimulationConfig` →
`validate_config` → `run_fmu` → `export_result`。独立编译成功后，先将其返回 FMU 按该顺序
导入。Project 工作流是：`open_project` →（若为 1.0，用户确认后）`migrate_project_schema` →
`compile_project_modelica` → 以返回 Project 更新状态 → `list_project_modelica_builds` 展示来源。

## 4. 参数编辑器和输入时序契约

`VariableMetadata.causality` 与 `variability` 的含义不同。是否可作为初始化参数覆盖由
`causality`（`parameter` 或支持的 `structuralParameter`）和类型/shape 共同决定，不能只凭
`causality` 把所有变量做成可编辑控件；初始化 input 仅限 `causality == "input"`。`variability`
描述变量变化特征，不是参数控件权限的唯一规则。枚举 metadata 未提供完整选项时，GUI 不得承诺
能构造完整下拉列表；数组也仅在后端实际支持的 FMI 类型、shape 与 runtime 范围内编辑。

参数覆盖是初始化时传入 `SimulationConfig.parameters`，并非运行中的实时改参。运行中输入更新仅用
`initial_inputs` 和有序的 `input_schedule`；最终有效性始终以公开
`backend.validate_config()` 为准。

对 `communication_step=0.01`，`InputUpdate(time=1.00, ...)` 的旧输入产生 `t=1.00`
样本；新输入在步区间 `[1.00, 1.01]` 生效，并影响 `t=1.01` 样本。时刻必须位于
`[start_time, stop_time)` 且与通信点对齐。GUI 应分别呈现“参数覆盖”和“运行时输入更新”，
不能混成一个控件。

## 5. 旧 `feat/frontend` 兼容性审计

只读审查的旧分支为 `43518ceec83236b27cd9413e545f1973fab014f0`，未合并、未切换，也未在
当前 Phase 9.6A 运行。审查文件为 `gui/main.py`、`gui/presenter.py`、
`gui/configuration_file.py`、`gui/plot_dialog.py`、`tests/integration/test_gui_public_workflow.py`
和 `tests/unit/test_gui_presenter.py`。

| 旧 GUI 面向的能力 | 代码审查结论 | 实际 GUI 验证 | 需修改文件 / 说明 |
|---|---|---|---|
| `create_backend()` | COMPATIBLE | NOT_VERIFIED | `gui/main.py` 通过公开工厂创建 Backend。 |
| `load_fmu()`、`validate_config()`、`run_fmu()` | COMPATIBLE（单 FMU） | NOT_VERIFIED | `main.py`、`presenter.py` 仅访问公开 DTO；`SimulationWorker(QThread)` 可复用为同步 run 的工作线程。 |
| `SimulationConfig` 基本字段、选中输出、参数/初始输入/JSON schedule 序列化 | ADAPTATION_REQUIRED | NOT_VERIFIED | `configuration_file.py` 的现有字段可构造当前 DTO；需改为遵循 9.4A/9.4B 的元数据、shape、类型与时序提示，且显示字段级 issues。 |
| FMU metadata 与绘图结果访问 | COMPATIBLE | NOT_VERIFIED | `presenter.py`、`plot_dialog.py` 访问 Farcel DTO，没有 FMPy 泄漏。 |
| QThread 运行机制 | COMPATIBLE | NOT_VERIFIED | `main.py` 已将单 FMU 运行移出主线程；编译、Project I/O 也必须采用同样模式并 marshal 回调。 |
| Project 管理、显式 schema 迁移 | NOT_IMPLEMENTED | NOT_VERIFIED | `main.py` 及相应测试需新增界面流程。 |
| Modelica 环境检测、`.mo` 编译、Project 编译登记 | NOT_IMPLEMENTED | NOT_VERIFIED | `main.py`/新 presenter 或 worker 层需新增公开 API 调用，不能自行启动 OMC。 |
| provenance、SHA-256 与已发布未登记构建诊断 | NOT_IMPLEMENTED | NOT_VERIFIED | 需新增 Project 来源展示视图；只读查询不得触发编译或删除。 |
| Graph/Worker 与完整 Project 编排 | NOT_IMPLEMENTED | NOT_VERIFIED | 不属于旧 GUI 的现有单 FMU 工作流。 |

旧 GUI 未发现对已经不存在的 API、DTO 或枚举字段的直接引用；但这不是“新版 GUI 可直接发布”的
结论。整体结论是：旧单 FMU 路径经代码审查为 `ADAPTATION_REQUIRED`，旧分支未合并且实际 GUI
运行仍为 `NOT_VERIFIED`。前端负责人必须完成上表所列文件的适配和独立 GUI 验收。

## 6. 错误到界面的稳定映射

界面依据 `EngineError.code`、非空 `message` 和 `details` 显示业务错误，不解析 traceback、
Python 异常类名或 OMC 原始 stderr。下表的公开行为已有自动化证据：本阶段消费者测试覆盖
`CONFIG_ERROR`、`PROJECT_IO_ERROR`、`PROJECT_FORMAT_ERROR` 和无 OMC 回退；其余证据为既有
单元/集成测试，避免在这里复制另一套实现。

| 错误码 | GUI 行为 | 自动化证据 |
|---|---|---|
| `CONFIG_ERROR` | 显示 `details["issues"]` 的字段、代码和消息。 | `test_modelica_frontend_public_workflow.py`、参数边界测试。 |
| `MODELICA_COMPILER_NOT_FOUND` | 提示选择存在的 OMC；不得回退到其他编译器。 | `test_modelica_frontend_public_workflow.py`、`test_modelica_compile_public_api.py`。 |
| `MODELICA_COMPILER_UNUSABLE` | 显示诊断并要求修复路径/可执行性。 | `test_openmodelica_compiler.py`。 |
| `MODELICA_OUTPUT_CONFLICT` | 要求选择空输出位置；不得覆盖现有 FMU。 | `test_modelica_asset_staging.py`、真实 `test_modelica_compile_public_api.py`。 |
| `PROJECT_CONFLICT` | 重新打开 Project 后由用户决定，不盲目保存/重试。 | `test_project_modelica_service.py`。 |
| `PROJECT_FORMAT_ERROR` | 告知文件结构无效；不直接修补 JSON。 | `test_modelica_frontend_public_workflow.py`、`test_json_project_repository.py`。 |
| `PROJECT_IO_ERROR` | 显示路径/持久化诊断；不把 I/O 问题伪装为编译成功。 | `test_modelica_frontend_public_workflow.py`、`test_project_public_api.py`。 |

## 7. 前端工作项与验收条件

1. **OMC 设置与状态。** 输入一个不存在路径时显示 `NOT_FOUND` 的稳定诊断，且仍能加载/运行
   已有 FMU；有效路径仅在后台探测，界面不自行运行 OMC。
2. **Modelica 编译入口。** 用户选择 `.mo`、模型类名、输出目录，在工作线程调用公开编译 API；
   完成后显示诊断并将成功 FMU 加入既有模型库。不得把 FMI 3、Model Exchange 或任意 Package
   误标为当前已支持编译目标。
3. **Project 1.0 升级确认。** 打开 1.0 仅提示；未确认时不改文件。确认后调用迁移 API，展示
   1.1 和备份位置，并重新打开验证一致性。
4. **Project 编译登记。** 仅对已打开的 1.1 Project 调用公开登记 API；成功后用返回 Project
   替换当前状态。发生冲突时不盲目保存。
5. **来源和哈希展示。** 将 provenance 以 `fmu_asset_id` 关联 `ModelAsset`，展示来源快照和
   SHA-256。已发布但未登记构建只显示后端诊断，GUI 不自行删除、恢复或重新编译。
6. **参数与输入控件。** 从 metadata 构造 `parameters`、`initial_inputs`、`input_schedule` 和
   `selected_outputs`；参数覆盖和运行时输入时序分区显示。非法类型、未知变量、错误 causality 或
   非通信点 schedule 必须显示后端字段级 issue。
7. **结果与绘图。** 只将 `SimulationResult` 交给既有绘图模块；导出由 `export_result()` 完成，
   不在 UI 线程写 CSV。
8. **线程与错误。** 编译、Project I/O、FMU 加载/运行/导出在 worker 执行；进度和结果回送主线程。
   不在主线程运行耗时 API，不从 stderr 判断错误状态。

## 8. 真实编译链路与本阶段可重复验证

真实 `.mo → FMU → inspect → run → export → Project 资产登记/来源追溯 → 相对路径移动`
链路沿用 Phase 9.5B 的已验收可选 OMC CI：Windows x64、Python 3.13、FMPy 0.3.31、
OpenModelica 1.26.3，31 个登记测试全部执行、0 skipped。它同时覆盖参数覆盖无需重编译、
同名输出冲突拒绝及真实 CLI/Project 入口；证据见
`PHASE_9_5B_OPTIONAL_OPENMODELICA_CI.md`。本阶段没有修改这些运行链路，故不重新把 OMC
设为默认 CI 依赖。

本阶段新增的普通消费者测试可在无 OMC、无 PySide6 环境执行：

```powershell
python -m unittest tests.integration.test_modelica_frontend_public_workflow -v
```

预期为 5 个测试全部通过，覆盖显式不存在 OMC、既有 FMU 加载/运行、metadata 驱动参数与输入
校验、显式 1.0→1.1 迁移、只读 provenance 和稳定错误负载。
