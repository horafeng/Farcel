# Phase 9.3B — Modelica Project CLI 工作流

## 范围

Phase 9.3B 在既有 `farcel modelica` 命令树中提供 Project schema 显式迁移、
Modelica 编译登记和来源查询。CLI 只调用 `create_backend()` 的公共方法：

```text
farcel modelica project migrate
farcel modelica project compile
farcel modelica project list
```

它不创建新的编译器、Project 服务、Repository 或 FMU runtime；也不直接读取、写入
`project.json`、生成 `.mos` 或运行 `omc.exe`。

## 显式迁移

```powershell
farcel modelica project migrate --project-root "D:\Projects\MyProject"
farcel modelica project migrate --project-root "D:\Projects\MyProject" --json
```

命令委托 `backend.migrate_project_schema(project_root)`。只有保存的 Project 1.0 能迁移；
不会创建 Project、不会自动升级，也不会重新迁移或降级 1.1 Project。迁移服务确定备份名称，
CLI 只显示其返回的相对路径。

成功 JSON：

```json
{
  "success": true,
  "project_root": "D:\\Projects\\MyProject",
  "schema_version": "1.1",
  "backup_relative_path": "project.json.1.0-backup-xxx.json"
}
```

现有模型资产、SimulationCase 和 run history 都由既有迁移服务保留。

## 编译并登记

```powershell
farcel modelica project compile `
  --project-root "D:\Projects\MyProject" `
  --source "D:\Models\FarcelFirstOrder.mo" `
  --model FarcelFirstOrder `
  --omc "D:\OpenModelica\bin\omc.exe" `
  --timeout 120 --json
```

参数：

| 参数 | 说明 |
| --- | --- |
| `--project-root PATH` | 必填，保存 `project.json` 的 Project 目录。 |
| `--source PATH` | 必填的单个 `.mo` 文件。 |
| `--model NAME` | 必填的普通或点分 Modelica 类名。 |
| `--omc PATH` | 可选显式 OMC 路径；无效路径不会回退。 |
| `--timeout SECONDS` | 可选，默认 `120`，由既有合同验证。 |
| `--json` | stdout 只输出一个 JSON 文档。 |

CLI 先调用 `open_project()`，构造既有 `ModelicaAssetStagingRequest`，再调用
`compile_project_modelica(project, request)` 一次。它不调用独立 `compile_modelica()`、不创建
`modelica-builds/`、不追加 JSON，也不会在登记后调用普通 `save_project()`。

Project 必须已经是 schema 1.1；schema 1.0 会被拒绝，用户必须先明确运行迁移命令。
编译固定为 FMI 2.0 Co-Simulation。成功 JSON 的主要字段：

```json
{
  "success": true,
  "project_id": "project-001",
  "schema_version": "1.1",
  "build_id": "<实际 build id>",
  "asset_id": "<实际 asset id>",
  "fmu_relative_path": "modelica-builds/build-.../model/model.fmu",
  "fmu_sha256": "<实际 FMU SHA-256>",
  "source_relative_path": "modelica-builds/build-.../source/model.mo",
  "source_sha256": "<实际源快照 SHA-256>",
  "model_name": "FarcelFirstOrder",
  "compiler_name": "OpenModelica",
  "compiler_version": "1.26.3"
}
```

所有资产、来源和散列均来自公共 API 返回的已验证登记结果；CLI 不计算或伪造散列。

## 查询来源

```powershell
farcel modelica project list --project-root "D:\Projects\MyProject"
farcel modelica project list --project-root "D:\Projects\MyProject" --json
```

命令调用 `open_project()` 和 `list_project_modelica_builds(project)`，只读取已经持久化的
provenance。它不需要 OpenModelica，不触发编译，不修改文件，也不扫描未知构建目录。
Project 1.0 或没有 Modelica 来源记录的 Project 返回空 `builds`：

```json
{
  "success": true,
  "project_id": "project-001",
  "schema_version": "1.1",
  "count": 1,
  "builds": [
    {
      "build_id": "<build id>",
      "asset_id": "<asset id>",
      "model_name": "FarcelFirstOrder",
      "compiler_name": "OpenModelica",
      "compiler_version": "1.26.3",
      "source_relative_path": "modelica-builds/build-.../source/model.mo",
      "source_sha256": "<source SHA-256>",
      "fmu_relative_path": "modelica-builds/build-.../model/model.fmu",
      "fmu_sha256": "<FMU SHA-256>"
    }
  ]
}
```

这里的 FMU 散列来自保存的 `ModelAsset`，来源散列来自保存的 provenance；本命令不重新计算
磁盘散列。

## 错误、冲突与退出码

运行时失败返回 `1`，argparse 参数错误或缺失 Project 子命令返回 `2`。普通模式以简体中文写入 stderr；JSON 模式
错误 stderr 只有一个稳定文档：

```json
{
  "success": false,
  "error": {
    "code": "PROJECT_CONFLICT",
    "message": "项目已被其他操作修改。",
    "details": {}
  }
}
```

错误 code/message/details 直接来自 `EngineError`，包括 `CONFIG_ERROR`、
`PROJECT_FORMAT_ERROR`、`PROJECT_IO_ERROR`、`PROJECT_CONFLICT`、
`MODELICA_COMPILER_NOT_FOUND`、`MODELICA_COMPILATION_ERROR`、
`MODELICA_STAGING_ERROR` 和 `TIMEOUT`。因此已有的条件保存保护不会被 CLI 绕过。

若构建包发布成功但 `project.json` 条件保存失败，CLI 返回错误而不伪装成功，并原样保留：

```text
details.stage
details.unregistered_build.build_id
details.unregistered_build.fmu_relative_path
```

它不会自动删除未登记构建包、重试覆盖 Project 或执行恢复；这些操作仍需后续明确功能。

## 验证

本机以 OpenModelica 1.26.3 和安装后的 `farcel` 入口验证：Project 1.0 → 显式迁移并检查备份
→ 真实 `FarcelFirstOrder.mo` 编译/登记 → 重开 Project → SHA-256 校验 → Farcel 仿真
（T=0.5、u=1、0–2 秒，最大绝对误差不超过 0.03）→ `project list --json` → 复制项目后的
来源查询与完整性验证。没有 OMC 的 CI 明确跳过这条真实链路，但迁移和 list 仍可用。

Phase 9.3C 如获单独授权，可处理后续 CLI 收尾；本阶段不实现 show、delete、recover、rebuild、
自动迁移或 GUI。
