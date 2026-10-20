# Phase 9.3A — Modelica CLI 环境检测与独立编译

## 范围

Phase 9.3A 为既有 Farcel CLI 增加 Modelica 二级命令。CLI 只调用 `create_backend()` 的公共 API；不导入 OpenModelica infrastructure、不运行 `omc.exe`、不生成 `.mos`，也不修改 Project 或 `project.json`。

```text
farcel modelica env
farcel modelica compile SOURCE_MO --model NAME --output-dir PATH
```

本阶段不提供 Project 编译/登记 CLI、Project schema 迁移 CLI、GUI 或异步编译。

## 环境检测

```powershell
farcel modelica env --omc "D:\OpenModelica\bin\omc.exe"
farcel modelica env --json
```

CLI 委托 `backend.get_modelica_environment(compiler_path=...)`。普通输出显示状态、编译器名称、版本和简体中文诊断；JSON 为一个完整文档：

```json
{
  "success": true,
  "status": "AVAILABLE",
  "compiler_name": "OpenModelica",
  "compiler_version": "1.26.3",
  "diagnostic_message": "OpenModelica 编译器可用。"
}
```

`status` 使用 `AVAILABLE`、`NOT_FOUND` 或 `UNUSABLE`。只有 `AVAILABLE` 返回退出码 0；未发现或不可用返回 1，但仍输出环境摘要以便诊断。

## 独立编译

```powershell
farcel modelica compile "examples/modelica/FarcelFirstOrder.mo" `
  --model FarcelFirstOrder `
  --output-dir "artifacts/modelica-cli" `
  --omc "D:\OpenModelica\bin\omc.exe" `
  --timeout 120 --json
```

| 参数 | 说明 |
| --- | --- |
| `SOURCE_MO` | 必填的单个 `.mo` 源文件。 |
| `--model NAME` | 必填的普通或点分 Modelica 类名。 |
| `--output-dir PATH` | 必填、且调用前必须存在的输出目录。 |
| `--omc PATH` | 可选显式 OMC 路径；无效路径不会回退。 |
| `--timeout SECONDS` | 可选，默认 `120`；由既有验证器检查。 |
| `--json` | stdout 只输出一个结构化 JSON 文档。 |

CLI 构造既有 `ModelicaCompileRequest` 后调用 `backend.compile_modelica()`。固定为 FMI `2.0` / Co-Simulation `cs`。成功 JSON：

```json
{
  "success": true,
  "fmu_path": "artifacts\\modelica-cli\\FarcelFirstOrder.fmu",
  "fmi_version": "2.0",
  "fmu_type": "cs",
  "compiler_version": "1.26.3",
  "sha256": "<64 位小写 SHA-256>"
}
```

`fmu_path` 使用当前平台的 `str(Path)` 表示。输出目录不自动创建；同名 FMU 由既有 adapter 返回 `MODELICA_OUTPUT_CONFLICT`，不会覆盖先前成功产物。

## 错误与退出码

Modelica 运行时错误返回 1，argparse 参数错误返回 2。普通模式错误写入 stderr。`--json` 模式成功时 stdout 不夹杂其他文字；失败时 stderr 只输出一个 JSON 文档：

```json
{
  "success": false,
  "error": {
    "code": "MODELICA_COMPILER_NOT_FOUND",
    "message": "未找到可用的 OpenModelica 编译器。",
    "details": {}
  }
}
```

错误 code 直接来自 `EngineError.code.value`，包括 `CONFIG_ERROR`、`MODELICA_COMPILER_NOT_FOUND`、`MODELICA_COMPILER_UNUSABLE`、`MODELICA_COMPILATION_ERROR`、`MODELICA_FMU_INVALID`、`MODELICA_OUTPUT_CONFLICT`、`MODELICA_OUTPUT_ERROR` 和 `TIMEOUT`。CLI 不将 OMC 原始 stdout/stderr 当作业务协议输出。

## 安全与验证

文件存在性、`.mo` 限制、模型类名、输出目录、超时、显式 OMC 路径和覆盖保护均由既有公共编译合同及 adapter 执行。CLI 不修改系统 PATH、不安装 OpenModelica、不创建输出目录，也不将独立产物登记到 Project。

本机通过安装后的 `farcel.exe`、OpenModelica 1.26.3 执行环境 JSON 与编译 JSON；检查 FMI 2.0 CS、SHA-256、既有 `inspect` / `run`，并确认第二次编译拒绝覆盖。真实测试以 `FARCEL_RUN_OPENMODELICA_INTEGRATION=1` 显式启用；没有 OMC 的 CI 只跳过真实命令测试，普通 CLI 单元测试不依赖 OMC。

## 后续

Phase 9.3B 可在单独授权后处理 Project CLI 工作流；本阶段没有实现它。
