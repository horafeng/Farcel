# Phase 9.2B — Modelica 源快照与 FMU 本地资产暂存

## 范围

本阶段实现一次本地、可验证的 Modelica 构建包暂存。它会生成 `ModelAsset` 和 `ModelicaBuildProvenance`，但二者只是内存中的已暂存描述：不会追加到 `SimulationProject.model_assets`，不会写入 `project.json`，也不会新增 `FarcelEngine`、CLI 或 GUI API。

## 职责与边界

`ModelicaAssetStagingService` 位于 application 层，只依赖 `ModelicaCompiler` 与 `ModelicaAssetStore` ports。它生成安全的构建 ID，编排快照、既有编译器、哈希一致性、`ModelAsset` 和 provenance 验证。

`LocalModelicaAssetStore` 位于 infrastructure/project，负责文件复制、受控目录、符号链接拒绝、哈希、目录发布和本次操作资源清理。它不导入 application，也不调用 OpenModelica。

既有 `OpenModelicaCompiler` 仍是唯一的 OMC 实现：暂存服务只把快照路径与私有编译输出目录构造成既有 `ModelicaCompileRequest`。没有第二套 `.mos` 或 FMU runtime。

## 目录结构与发布

一次成功的构建包布局为：

```text
project-root/
  project.json                    # 本阶段绝不写入
  modelica-builds/
    build-<32 位随机十六进制 ID>/
      .farcel-modelica-staging    # 受控归属标记
      source/
        model.mo
      model/
        model.fmu
  results/
```

编译前的私有工作目录位于 `project-root/.farcel-modelica-staging/`。它包含相同的归属标记、源快照和独立的 `compiler-output/`。该目录仅是未发布的私有编译输出，绝不会进入正式构建包；正式文件只使用固定受控名称，用户路径和模型名不会直接构成目录名。

源文件按“原文件 SHA-256 → 排他复制 → 快照 SHA-256 → 原文件再次 SHA-256”校验。任一不一致都会失败，且不会宣称快照可信。编译前后再次校验快照，因此 OMC 读取的是本次保存的快照，而非用户后来修改的原始 `.mo`。

FMU 必须位于当前工作目录的 `compiler-output/` 内，是普通文件，并与 `ModelicaCompileResult.sha256` 一致。复制到 `model/model.fmu` 后再次计算散列、再次验证源快照，再安全删除受控的 `compiler-output/`。删除失败、目录解析越界或剩余文件结构不符合冻结布局时，发布立即以 `MODELICA_STAGING_ERROR` 失败。`ModelAsset.sha256` 是 FMU 哈希的唯一权威；provenance 不保存第二份 FMU 散列。

同一文件系统内，在目标 `build-<id>` 不存在时，最终使用目录 rename 发布，因此完整构建包对读取方一次可见。发布前会白名单验证目录只包含归属标记、`source/model.mo` 与 `model/model.fmu`。它不是跨文件系统事务，也不把普通 `open("xb")` 文件复制描述为原子发布；不同平台对目录 rename 的覆盖语义也不相同，因此发布前后均明确拒绝同名目标。构建 ID 由 UUID 生成，目标已存在立即返回 `MODELICA_OUTPUT_CONFLICT`，绝不覆盖。

发生编译或发布失败时，服务只删除带匹配归属标记的当前未发布工作目录。清理失败会保留可识别的暂存目录以供显式恢复，绝不扫描或删除未知目录。若发布后本地 `ModelAsset` 完整性复核失败，仅移除带匹配标记的刚发布构建包。

## 结果与持久化边界

`ModelicaStagedBuild` 返回：

- `ModelAsset`：`asset_id` 为本次构建 ID 派生值，路径为项目相对 FMU 路径。
- `ModelicaSourceSnapshot`：项目相对 `source/model.mo` 路径及快照 SHA-256。
- `ModelicaBuildProvenance`：同一 `build_id`、真实 adapter 提供的编译器名称、实际编译器版本、FMI 2.0、CS 及相同的 `fmu_asset_id`。

这表示“文件已安全暂存”，不表示“Project 已登记”或“来源已持久化”。Project 1.0、`LocalJsonProjectRepository`、运行历史和 `project.json` 字节内容均保持原状。由于所有引用均为 `/` 分隔的项目相对路径，整个项目目录移动后仍可用既有 `ProjectAssetValidator` 重新解析和校验。

## 错误语义

现有编译请求、OMC、超时和 FMU 编译错误保持原有 `EngineError` 语义。新增 `MODELICA_STAGING_ERROR` 用于受控的源快照、工作目录、FMU 哈希、路径或发布完整性失败；目标构建目录冲突复用 `MODELICA_OUTPUT_CONFLICT`。不会把原始 `OSError` 或 OMC 日志作为公共错误协议。

## 测试与真实验证

普通单元和 application 测试覆盖快照散列、原始文件修改后的快照稳定性、实际使用快照作为 compiler 输入、FMU 散列、来源关联、冲突拒绝、暂存清理、符号链接条件测试、project.json 字节不变以及 Project 1.0 回归。

可选真实集成测试通过 `FARCEL_RUN_OPENMODELICA_INTEGRATION=1` 启用，使用 OpenModelica 1.26.3 编译 `FarcelFirstOrder.mo`，随后由既有 Farcel API 加载、运行并检查一阶系统理论误差不超过 0.03，同时验证项目移动后的相对路径和散列。

Windows 本机已实际运行新增暂存测试、Phase 9.0--9.2A 相关回归及完整后端回归；受 Windows 开发者模式/符号链接权限影响的符号链接测试会明确条件跳过，其余测试均通过。真实集成测试已实际使用 OpenModelica 1.26.3，并断言发布目录不包含 `compiler-output/`。

## 9.2C 前置条件

9.2C 必须在本构建包与 DTO 已验收后，才实现 schema 1.1 的读取、显式迁移和保存。该阶段需要单独定义持久化失败恢复；9.2B 不会进行隐式迁移或自动注册。
