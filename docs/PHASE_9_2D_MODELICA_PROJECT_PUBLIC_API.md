# Phase 9.2D — Modelica Project 编译、资产登记与公共 API

## 已实现范围

Phase 9.2D 将既有 OpenModelica 编译器、9.2B 已验证构建包暂存与 9.2C schema 1.1 provenance
持久化接入默认 `create_backend()`。它不新增编译器、Project Repository 或 FMU runtime。

公共 API：

```python
registration = backend.compile_project_modelica(project, request)
updated_project = registration.project
staged_build = registration.staged_build

migration = backend.migrate_project_schema(project_root)
provenances = backend.list_project_modelica_builds(updated_project)
```

`request` 是既有 `ModelicaAssetStagingRequest`。`registration` 是
`ProjectModelicaCompilation`，它清楚区分更新后的不可变 Project 和本次已验证、已登记的
`ModelicaStagedBuild`。

## 编排与登记

`ProjectModelicaService` 位于 application 层，只依赖：

```text
ModelicaAssetStagingService + ProjectRevisionRepository + ProjectValidator
```

流程为：读取带内容散列的持久化 Project → 比对调用者 Project → 验证 schema 1.1 Project →
暂存并验证源快照/FMU 构建包 → 追加 `ModelAsset` 与 `ModelicaBuildProvenance` → 完整 Project
验证 → 条件保存 project.json。

`ModelAsset` 与 provenance 都只来自已发布、已校验的构建包；已有资产、Case、Graph、
run history 与旧 provenance 均原样保留。不会自动创建 Case 或修改 Graph。

## Schema 和迁移

Project 1.1 可以登记。Project 1.0 被 `CONFIG_ERROR` 拒绝，并提示先显式调用
`migrate_project_schema()`；此 API 仅委托 9.2C 的 `ProjectSchemaMigrationService`，不重新实现
迁移规则。Project 读取、普通保存和普通 FMU 项目仍保持原行为，绝不自动升级。

来源查询通过 `list_project_modelica_builds(project)` 返回 Project 自有 provenance 元组：它没有
I/O、副作用、OMC 依赖或临时目录扫描；空元组表示普通 FMU 项目没有 Modelica 来源记录。

## 乐观并发与失败边界

Repository 新增实现无关的 `ProjectRevisionRepository`：

1. `load_with_revision()` 返回 Project 和原始 project.json 的 SHA-256。
2. `save_if_unchanged()` 在同目录临时文件 fsync 后，最终再次核对 SHA-256。
3. 内容变化返回 `PROJECT_CONFLICT`，拒绝覆盖最新文件。

普通 `save_project()` 未改变。最终散列检查到 `os.replace()` 之间仍存在跨进程 TOCTOU 窗口；
这是乐观保护，不是数据库事务或文件锁承诺。

编译/暂存失败时 project.json 不被修改。若构建包发布成功但 Project 条件保存失败，后端保留
该已验证但未登记的构建包，返回原稳定错误码，并在 details 中提供：

```text
stage = "project_persistence"
unregistered_build.build_id
unregistered_build.fmu_relative_path
```

本阶段不扫描、自动登记或自动删除这种构建包，避免误删用户或并发任务的数据。

## 前后端边界

GUI/CLI → application → contracts ← infrastructure 保持不变。Application 不导入
`OpenModelicaCompiler` 或 JSON Repository；Infrastructure 不导入 Application；contracts 不包含
OMC、subprocess、FMPy 或 Qt 类型。`create_backend()` 只装配依赖，不会启动 OMC；OMC 仅在明确
调用 Project 编译时启动。

PySide6 前端必须在工作线程执行编译，依据 `EngineError.code/message/details` 显示错误，不能
直接运行 `omc.exe`、解析编译日志或写 project.json。

## 验证

自动化测试覆盖 schema 1.0 拒绝、显式迁移、普通登记、资产/provenance 保留、陈旧调用者、
持久化冲突、编译/暂存失败、未登记构建包错误详情、手动 Engine 未配置服务和架构边界。

本机 OpenModelica 1.26.3 端到端测试通过：schema 1.0 → 显式迁移 → 默认 backend 编译
FarcelFirstOrder.mo → Project 登记/重开 → 哈希校验 → Farcel 仿真（T=0.5、u=1、0–2 s）→
项目移动后复验。最大绝对误差要求为不超过 0.03。

## 当前限制

没有自动迁移、自动登记、构建包垃圾回收、异步 API 或 GUI。未登记构建包恢复、Project 资产
工作流的进一步收尾须在后续明确阶段处理；本阶段不进入 Phase 9.3。
