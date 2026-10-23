# Phase 9 — 前端交付说明

前端实现者先阅读 `FRONTEND_BACKEND_INTEGRATION.md` 第 19–21 节、
`PHASE_9_1D_MODELICA_PUBLIC_API.md`、`PHASE_9_2D_MODELICA_PROJECT_PUBLIC_API.md`、
`PHASE_9_3A_MODELICA_CLI.md`、`PHASE_9_3B_MODELICA_PROJECT_CLI.md` 和
`PHASE_9_BACKEND_FINAL_ACCEPTANCE.md`。

## 边界与 DTO

前端只使用 `create_backend()`、`farcel.contracts` 和 `EngineError`。不得导入
`farcel.infrastructure`、运行 OMC、生成 `.mos`、解析 OMC 日志或直接修改 `project.json`。

使用 `ModelicaEnvironmentInfo` / `ModelicaEnvironmentStatus` 查询环境，
`ModelicaCompileRequest` / `ModelicaCompileResult` 完成独立编译，
`ModelicaAssetStagingRequest` / `ProjectModelicaCompilation` 完成 Project 登记。
来源展示使用 `ModelicaSourceSnapshot`、`ModelicaBuildProvenance`、`ModelicaStagedBuild` 和
`ModelAsset`。

## GUI 工作流

1. 调用 `backend.get_modelica_environment(compiler_path=...)` 显示 `AVAILABLE`、`NOT_FOUND` 或
   `UNUSABLE` 的中文诊断。
2. 用户选择 `.mo`、模型名和独立输出目录时，在工作线程调用
   `backend.compile_modelica(ModelicaCompileRequest(...))`；成功 FMU 交给既有导入/仿真流程。
3. Project 工作流先 `backend.open_project(project_root)`。若 schema 是 1.0，必须得到用户确认后
   显式调用 `backend.migrate_project_schema(project_root)`，不得自动迁移。
4. 在工作线程调用
   `backend.compile_project_modelica(project, ModelicaAssetStagingRequest(...))`。以
   `registration.project` 更新 GUI，以 `registration.staged_build.model_asset` 更新模型库。
5. 用 `backend.list_project_modelica_builds(project)` 只读展示来源，并用
   `provenance.fmu_asset_id` 关联 `project.model_assets`。不需要 OMC。
6. 新 `ModelAsset` 可像普通 FMU 一样供用户加入模块库和 Graph；不会自动创建 Case 或连接。

参数编辑、模型框/端口、Graph 连接和图表是前端职责，继续使用现有 Graph/Simulation DTO。

## 错误和线程

编译同步且耗时，必须在 PySide6 工作线程执行，完成结果再回送 GUI 线程。按
`EngineError.code`、`message`、`details` 显示错误，不能从日志文本判断业务状态。

- `CONFIG_ERROR`：显示字段级 `details["issues"]`；schema 1.0 编译提示先迁移。
- `MODELICA_COMPILER_NOT_FOUND` / `MODELICA_COMPILER_UNUSABLE`：提示选择可用 OMC。
- `MODELICA_OUTPUT_CONFLICT`：要求用户选择不同输出，不能覆盖。
- `PROJECT_CONFLICT`：提示重新打开 Project，不盲目重试。
- 已发布未登记构建包：保留 `details.stage`、`unregistered_build.build_id` 和
  `fmu_relative_path` 以供诊断；当前没有自动恢复/删除。

用户提供的绝对 OMC 路径只用于当前机器，不会作为 Project provenance 保存。

## 当前未提供

没有 GUI 源码编辑器、多文件 Modelica Package 编译、FMI 1/3 或 Model Exchange 编译、自动迁移、
构建包自动恢复/删除/重编译、后台队列 API 或 Project JSON 直接编辑。
