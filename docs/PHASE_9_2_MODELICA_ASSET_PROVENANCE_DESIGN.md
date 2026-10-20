# Phase 9.2 — Modelica 源文件与 FMU 来源设计

## 目标与阶段边界

Phase 9.2 为由 Modelica 源文件编译得到的 FMU 建立可追溯的项目资产模型。本文件冻结的是持久化前的合同和演进策略；它不改变当前 Project schema 1.0，也不复制源文件、登记资产或写入 `project.json`。

阶段建议划分如下：

| 阶段 | 范围 |
| --- | --- |
| 9.2A（当前） | 不可变 provenance DTO、纯验证和 schema 演进设计。 |
| 9.2B（已实现） | 受控源快照、既有编译器复用、已验证 FMU 构建包暂存；不登记到 Project JSON。 |
| 9.2C（后续） | 设计、实现并测试显式的 Project schema 版本读取、保存和迁移。 |
| 9.2D（后续） | 在上述能力稳定后，考虑面向 Project 的编译与来源查询编排 API。 |

这些后续阶段均不是当前提供的功能。

## 已实现的合同

`farcel.contracts` 正式导出以下纯数据对象，均为 `frozen=True, slots=True` 的 dataclass：

### `ModelicaSourceSnapshot`

| 字段 | 语义 |
| --- | --- |
| `relative_path: str` | 项目内 `.mo` 源文件快照的规范相对路径，使用 `/`。 |
| `sha256: str` | 此源文件内容的 64 位小写十六进制 SHA-256。 |

它标识一次编译实际依赖的源内容，不是用户原始绝对路径，也不表示“始终跟随用户后来修改的文件”。DTO 本身不读取任何文件。

### `ModelicaBuildProvenance`

| 字段 | 语义 |
| --- | --- |
| `build_id` | 稳定、非空的编译记录标识。 |
| `source_snapshot` | 编译所用的 `ModelicaSourceSnapshot`。 |
| `model_name` | 第一版受支持的普通或点分 Modelica 类名。 |
| `compiler_name` | 编译器的产品名称，例如 `OpenModelica`。 |
| `compiler_version` | 编译时检测到的编译器版本文本。 |
| `fmi_version` | 当前固定为 `"2.0"`。 |
| `fmu_type` | 当前固定为 `"cs"`。 |
| `fmu_asset_id` | 对应既有 `ModelAsset.asset_id` 的关联键。 |

`ModelAsset.sha256` 是 FMU 文件内容散列的唯一权威来源。`ModelicaBuildProvenance` 有意不保存第二份 FMU SHA-256，避免两份散列发生冲突。第一版也不推测目标平台、不保存 `omc.exe` 绝对路径、临时目录、超时设置、子进程对象或原始 OMC 日志。

该记录支持来源追溯和将来的重新编译请求比较，但不承诺不同操作系统、编译器版本或工具链一定产生字节完全相同的 FMU。

## 纯验证规则

`farcel.application.modelica_provenance_validation.validate_modelica_build_provenance()` 不读取或写入文件，也不会启动 OpenModelica。它返回既有 `ValidationReport` 和字段级 `ValidationIssue`；`require_valid_modelica_build_provenance()` 仅将无效报告映射为既有 `EngineError(ErrorCode.CONFIG_ERROR)`。

源快照路径复用 Project 资产的低层纯规则：必须是非空的规范项目相对路径，不能含反斜杠、NUL、`.`、`..`、空路径段、POSIX 绝对路径、Windows 盘符或 UNC 路径。散列必须是 64 位小写十六进制。来源记录还会检查非空 `build_id`、模型名、编译器名称和版本，限制 FMI 2.0 / Co-Simulation，并要求非空 `fmu_asset_id`。

调用者提供当前 Project 的 `model_assets` 时，验证器还会报告未知或重复的 `fmu_asset_id`；它不会由此推断 FMU 或源文件现在仍存在，也不会验证其磁盘内容。

`ModelicaCompileRequest` 是一次运行时编译请求，包含源/输出/编译器路径和超时等本机执行信息，不能直接作为持久化 provenance。`ModelicaBuildProvenance` 仅保留跨项目移动仍有意义的稳定事实。

## 与现有 Project 1.0 的关系

当前 `PROJECT_SCHEMA_VERSION` 仍为 `"1.0"`，本阶段没有修改 `SimulationProject`、`ModelAsset`、`ProjectRunArtifact`、`LocalJsonProjectRepository` 或 `project.json` 编码。普通手工导入的 FMU 保持没有 Modelica 来源记录且继续完全可用。

后续 schema 演进建议在新 schema 版本中增加独立的顶层 provenance 集合，例如 `modelica_build_provenance`，通过 `fmu_asset_id` 关联资产，而不是强制为每个 `ModelAsset` 构造函数增加必填字段。这样既可以让不具来源的既有 FMU 保持合法，也不改变 `SimulationGraph` 依其 `model_path` 匹配 `ModelAsset.relative_path` 的现有规则。

建议的兼容策略：

1. 1.0 读取器继续按当前严格规则读取和保存 1.0 文档。
2. 将来的读取器把 1.0 项目解释为“没有 provenance 记录”，无需自动迁移即可运行。
3. 从 1.0 升级到新版本必须是明确、可见并经过测试的操作；不能因一次普通保存而悄然改写 schema。
4. 新版本保存不得覆盖或改变旧项目的运行历史；迁移前应保留可恢复副本，并定义临时文件/原子替换策略。

## 候选目录与移动性

9.2B 最终采用版本化构建包布局：

```text
project-root/
  project.json
  modelica-builds/
    build-<unique-id>/
      source/model.mo
      model/model.fmu
  results/
    ... existing run artifacts
```

来源记录只保存 `/` 分隔的项目内相对路径。项目整体移动后，解析应以新的 project root 为基准，而不是使用开发者机器的工作目录或 OpenModelica 安装路径。

9.2B 的实际暂存目录为 project root 内受控的 `.farcel-modelica-staging/`，正式构建目录只在源快照与 FMU 均已校验后以同文件系统目录 rename 发布。详见 `PHASE_9_2B_MODELICA_ASSET_STAGING.md`。

## 冻结的 schema 1.1 草案（尚未实现）

9.2C 建议将 `PROJECT_SCHEMA_VERSION` 升级为 `"1.1"`，但 9.2B 不改变当前值或 repository。新增顶层可选集合 `modelica_build_provenance`，编码形状建议为：

```json
{
  "schema_version": "1.1",
  "model_assets": [],
  "modelica_build_provenance": [
    {
      "build_id": "<32 位构建 ID>",
      "source_snapshot": {
        "relative_path": "modelica-builds/build-<id>/source/model.mo",
        "sha256": "<source sha256>"
      },
      "model_name": "Example.FirstOrder",
      "compiler_name": "OpenModelica",
      "compiler_version": "1.26.3",
      "fmi_version": "2.0",
      "fmu_type": "cs",
      "fmu_asset_id": "modelica-build-<id>"
    }
  ]
}
```

`ModelAsset.sha256` 继续是 FMU 唯一散列权威。1.0 项目被解释为没有 provenance 的合法项目；不会自动写成 1.1。显式迁移必须先验证现有 Project、复制或备份原始 `project.json`，在临时 JSON 中验证完整 1.1 文档，再以可恢复替换发布。迁移或保存失败时保留原 1.0 文档、运行历史和已暂存构建包；不会删除资产目录或尝试隐式回滚未知文件。

## 安全性、一致性与故障边界

后续实施必须至少满足以下边界：

1. 拒绝路径穿越、绝对路径和非规范分隔符；解析时继续确认候选路径位于 project root 内。
2. 在文件操作阶段防止符号链接解析后越出 project root。
3. 复制/载入源快照时重新计算 SHA-256，失配不能登记为该快照。
4. 登记 FMU 时复用 `ModelAsset.sha256` 验证，失配不能建立 provenance 关联。
5. 源文件后来被修改不改写既有快照；其散列保留历史来源证据。
6. 同名目标须拒绝覆盖或采用明确的、经过验证的唯一命名策略，不能损坏原资产。
7. 编译或 FMU 验证失败时不得登记不存在或未验证的资产。
8. 资产注册与 `project.json` 保存需要定义一致性边界，避免只完成其中一半。
9. 中途崩溃后的临时文件恢复和清理必须只作用于本次操作可识别的临时资源，不能删除用户资产。
10. 既有项目和运行历史在新增来源功能时不得被无意改写。

## 测试与 9.2B 前置条件

本阶段测试覆盖 DTO 的不可变性和字段集合、合法记录、路径安全、源散列、模型与编译形状、资产关联的未知/歧义情况、多错误收集、`CONFIG_ERROR` 映射以及无文件写入副作用。同时回归现有 Project 资产、Project 持久化和 Modelica 公共 API 测试。

2026-10-20 的验证记录：新增 contracts/application 测试与 Project 边界回归共 56 项成功（1 项符号链接权限条件跳过）；本机还执行了真实 OpenModelica 1.26.3 的既有公共 API 编译、加载、运行和 CSV 导出回归。GitHub Actions run 118 在 Windows Python 3.10 的完整套件中报告 796 项成功、4 项明确跳过；Python 3.13 的对应 job 亦成功。

9.2B 开始前必须先验收这些合同、确定新 schema 版本的精确编码及显式迁移策略，并定义资产文件写入和 `project.json` 持久化之间可恢复的一致性流程。
