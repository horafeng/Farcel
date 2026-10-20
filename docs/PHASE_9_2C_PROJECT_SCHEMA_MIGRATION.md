# Phase 9.2C — Project schema 1.1 与 Modelica 来源持久化

## 已实现范围

Project schema 仍默认创建和保存为 `1.0`。schema `1.1` 是明确指定或显式迁移后才会产生的格式；本阶段没有新增 FarcelEngine Project 编译 API，也不会扫描或自动登记 9.2B 暂存目录。

`SimulationProject` 新增可选、不可变的 `modelica_build_provenance` 元组。该字段位于既有 `schema_version` 字段之后，因此旧位置参数构造保持含义不变。`PROJECT_SCHEMA_VERSION` 继续为 `"1.0"`；`LATEST_PROJECT_SCHEMA_VERSION` 为 `"1.1"`，`SUPPORTED_PROJECT_SCHEMA_VERSIONS` 明确列出可读版本。

## JSON 格式

1.0 顶层字段保持完全不变，不能包含 provenance 字段。1.0 DTO 若携带非空 provenance，保存会以 `PROJECT_FORMAT_ERROR` 拒绝，绝不静默丢失数据。

1.1 在既有顶层字段外严格增加唯一字段：

```json
{
  "schema_version": "1.1",
  "project_id": "example",
  "name": "示例项目",
  "model_assets": [],
  "simulation_cases": [],
  "run_history": [],
  "modelica_build_provenance": [
    {
      "build_id": "0123456789abcdef0123456789abcdef",
      "source_snapshot": {
        "relative_path": "modelica-builds/build-0123456789abcdef0123456789abcdef/source/model.mo",
        "sha256": "<64 位小写 SHA-256>"
      },
      "model_name": "Example.FirstOrder",
      "compiler_name": "OpenModelica",
      "compiler_version": "1.26.3",
      "fmi_version": "2.0",
      "fmu_type": "cs",
      "fmu_asset_id": "modelica-build-0123456789abcdef0123456789abcdef"
    }
  ]
}
```

未知 schema、未知字段、缺失的 1.1 provenance 字段和额外的 1.0 provenance 字段都以 `PROJECT_FORMAT_ERROR` 拒绝。`ModelAsset.sha256` 仍是 FMU 内容散列的唯一权威；来源记录不保存第二份 FMU 散列，也不会保存 OMC 绝对路径、临时目录或原始日志。

## 完整性与移动性

`ProjectValidator` 会验证 provenance 的 build ID、模型名、FMI 2.0/CS、FMU asset 引用及唯一性；同时检查源快照的安全项目相对路径、普通文件、SHA-256 和 9.2B 固定构建包关系：

```text
modelica-builds/build-<build-id>/source/model.mo
modelica-builds/build-<build-id>/model/model.fmu
```

FMU 完整性继续复用 `ProjectAssetValidator`。验证不写入文件、不重新编译，也不会追踪或修改运行历史。所有路径均相对于 project root，因此复制或移动整个项目目录后可再次验证。

## 显式迁移

`ProjectSchemaMigrationService` 仅通过 `ProjectSchemaMigrationRepository` Port 调用，当前未装配到 Engine：

1. Repository 读取严格的 1.0 JSON 并返回内容散列保护的候选。
2. Application 使用 `ProjectValidator` 验证旧项目。
3. Repository 创建 `project.json.1.0-backup-<原文 SHA-256>.json`；使用排他创建，绝不覆盖不同内容。
4. 生成、解析自检完整 1.1 JSON；写入同目录临时文件并 fsync。
5. 再次比对原 project.json 散列，未变化时才用 `os.replace()` 发布。

普通 `open_project()` 和 `save_project()` 不触发迁移。已经是 1.1、未知版本、候选文件已变更或迁移前验证失败都会拒绝。迁移只添加空 provenance 集合，保留既有 ModelAsset、SimulationCase、RunHistory、results 和暂存构建包。

备份成功与 JSON 替换不是多文件数据库事务；`os.replace()` 仅在同一文件系统提供单文件替换语义，外部进程在最终检查与替换之间的竞争无法完全消除。失败时原 1.0 文档保持不变或保留可恢复备份；不会删除资产和运行历史。

## 验证

本机完整后端回归共 825 项测试通过；自动化测试覆盖 1.0 保持原样、1.1 空/非空往返、中文、严格字段、来源关联、路径逃逸、散列失配、缺失文件、项目移动、显式迁移、备份、并发修改检测与替换失败保护。

真实 OpenModelica 1.26.3 端到端测试在本机通过，执行 1.0 显式迁移、9.2B 源快照/FMU 暂存、1.1 provenance 保存和重开、FMU 运行以及项目移动后的验证。GitHub CI 无 OMC 时该真实测试保持显式跳过。

## 后续

Phase 9.2D 才会在单独授权下提供 Project 编译与资产登记业务流程；本阶段不新增该公共 API。
