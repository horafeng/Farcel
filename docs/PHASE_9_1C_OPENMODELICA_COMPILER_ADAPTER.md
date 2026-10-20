# Phase 9.1C — OpenModelica 编译器适配器

## 范围

本阶段在 infrastructure 层提供 `OpenModelicaCompiler`。它实现
`ModelicaCompiler` Port，并复用 Phase 9.1B 的
`ModelicaCompileRequest`、`ModelicaCompileResult`、`ModelicaDiagnostic` 和
`require_valid_modelica_compile_request()`；没有新增 FarcelEngine、CLI 或 GUI
编译 API。

依赖方向保持为：

```text
GUI / CLI -> application -> contracts <- infrastructure/openmodelica
```

适配器不依赖 OMPython、FMPy、Qt 或 `subprocess` 以外的进程管理框架。

## 支持的请求与边界

请求仍由 Phase 9.1B 的不可变 DTO 表示：单一 `.mo` 源文件、单一普通或点分
限定模型名、FMI `2.0`、Co-Simulation `cs`，以及本地同步编译。`output_directory`
必须已经存在。

适配器与 application 共同调用 `contracts.modelica_validation` 的同一套纯验证规则；
application 保留原有公开验证函数及 `CONFIG_ERROR` 映射，adapter 也为直接调用方保留
相同的稳定错误语义。它不会在验证阶段创建或删除文件，也不会把
原始 shell 参数、OMC 原生命令或脚本片段暴露给调用者。带引号的 Modelica 类名和
复杂 Modelica 类名语法仍不在支持范围内。模型语义、第三方库依赖和类是否真正存在
由 OMC 的 `loadFile()` / `checkModel()` 判断。

## OMC 发现与执行

显式 `compiler_path` 具有严格优先级：无效时立即报错，绝不退回自动发现。未显式
提供时，复用 Phase 9.1A 检测器依次检查 `OPENMODELICAHOME`、`PATH` 和常见
Windows 安装目录。候选程序必须实际通过 `omc --version` 启动，并返回可识别版本。
生产代码不会写死 `D:\OpenModelica`，不会修改 PATH，也不会安装编译器。

每次编译创建独立临时工作目录和受控 UTF-8 `.mos` 文件。该脚本依序执行：

1. `cd()` 到工作目录；
2. `loadFile()`；
3. `checkModel()`；
4. `buildModelFMU(..., version="2.0", fmuType="cs")`；
5. 输出受控标记及 `getErrorString()` 诊断区段。

文件系统路径经过 Modelica 字符串转义，支持空格和 Unicode；控制字符会被拒绝。进程
使用参数列表和 `shell=False` 启动，捕获 stdout、stderr、退出码并采用请求的超时。

超时时，Windows 上适配器以 `taskkill /PID /T /F` 尽力结束 OMC 的子进程树，随后
再次终止 OMC 主进程；非 Windows 使用独立会话的进程组。该措施是尽力清理：如果
编译器把工作交给脱离自身树的外部进程，操作系统仍可能留下该外部进程，不能将其宣称为
绝对保证。

## FMU 验证、哈希与发布

FMU 始终先在隔离工作目录中生成。返回成功前，适配器验证：

- `loadFile()` 返回 `true`，且 `checkModel()` 返回该模型的成功结果；
- OMC 报告并真实生成非空 FMU；
- 产物为可读取 ZIP，包含可解析的 `modelDescription.xml`；
- FMI 版本为 `2.0`，包含 `CoSimulation` 和合法 `modelIdentifier`；
- 当前 Windows x64 上包含 `binaries/win64/<modelIdentifier>.dll`。

验证后使用流式 SHA-256 计算散列。发布使用排他创建（`xb`），因此不会覆盖用户或
并发任务已经存在的同名 FMU；该方式不是“从出现起即完整”的原子替换。发布后再次计算
SHA-256，并要求两次结果一致。失败时仅尝试删除当前任务创建的不完整目标文件，不会删除
既有文件或其他任务的文件。

## 稳定错误和诊断

在既有 `EngineError` / `ErrorCode` 中新增下列稳定错误码：

| 错误码 | 适用阶段 |
| --- | --- |
| `MODELICA_COMPILER_NOT_FOUND` | OMC 未发现或显式路径无效 |
| `MODELICA_COMPILER_UNUSABLE` | 版本检查或进程启动失败 |
| `MODELICA_COMPILATION_ERROR` | 源文件加载、模型检查、构建或进程失败 |
| `MODELICA_FMU_INVALID` | FMU ZIP、metadata 或 Windows DLL 验证失败 |
| `MODELICA_OUTPUT_CONFLICT` | 目标同名 FMU 已存在 |
| `MODELICA_OUTPUT_ERROR` | 发布或发布后 SHA-256 检查失败 |

请求无效继续沿用 `CONFIG_ERROR`，超时沿用 `TIMEOUT`。错误 details 包含稳定的
`stage`，而非要求消费者解析 OMC 输出。成功结果提供一条
`MODELICA_FMU_COMPILED` INFO 诊断；原始 OMC 输出仅在当前进程内部用于受控结果判断，
不作为公共诊断直接返回。

## 测试与真实环境

`tests/unit/test_openmodelica_compiler.py` 使用 mock 进程和临时目录，覆盖发现优先级、
加载/检查/构建失败、非零退出、超时、损坏或不兼容 FMU、Windows DLL、覆盖保护、
发布散列、临时目录清理、Unicode/空格路径和点分模型名。

`tests/integration/test_openmodelica_compiler.py` 仅在
`FARCEL_RUN_OPENMODELICA_INTEGRATION=1` 时运行。它以测试专用默认路径
`D:\OpenModelica\bin\omc.exe`（或 `FARCEL_OPENMODELICA_OMC` 覆盖）编译
`FarcelFirstOrder.mo`，然后通过现有 Farcel 公共 API 加载、运行并按
`y(t)=1-exp(-t/0.5)` 验证数值误差不大于 `0.03`。该开发机记录的 OMC 版本为
OpenModelica `1.26.3`。未安装 OMC 的 CI 会明确跳过该真实测试。

Phase 9.1C 尚未开放 `FarcelEngine.compile_modelica()`；将 adapter 接入正式应用服务
与用户界面仍是后续 Phase 9.1D 的前置工作，不能在本阶段提前实现。

## CI 修复记录（Phase 9.1C-FIX）

GitHub Actions run `39374057844` 的 Windows Python 3.10 和 3.13 均出现相同的两个
失败：`test_infrastructure_does_not_import_application` 与
`test_explicit_valid_path_runs_version_check`。前者的根因是 adapter 直接导入
`farcel.application.modelica_validation`，违反 infrastructure 只能依赖 contracts 的
边界。修复后，纯请求规则位于 `contracts.modelica_validation`；application 仍提供既有
公开验证入口和 `CONFIG_ERROR` 映射，adapter 直接复用同一规则并为不经 application 的
调用保持相同的安全检查。公共 API 未改变。

后者是 Windows GitHub Runner 可能将临时路径表示为 8.3 短路径，而 adapter 规范化为
长路径。测试现改为在临时文件仍存在时，以 `executable.resolve()` 生成预期规范路径，
继续断言显式路径与 `shell=False`。该更改不调整生产发现语义。

本修复的本地测试结果与新的 GitHub Actions run 将在完成推送后记录；未读取到远端结果
前不得把 CI 视为通过。
