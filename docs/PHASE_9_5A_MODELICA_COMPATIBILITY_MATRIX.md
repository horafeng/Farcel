# Phase 9.5A：OpenModelica / Farcel 真实兼容性矩阵

## 1. 范围与结论

本记录扩大了真实 Modelica 覆盖面，但没有引入新的运行时或编译能力。所有 `PASS`
都来自同一台 Windows x64 开发机上的真实 OpenModelica 编译产物，并通过 Farcel 的公开
Backend API 完成 `get_modelica_environment()`、`compile_modelica()`、`load_fmu()`、
`validate_config()`、`run_fmu()` 和（动态模型）`export_result()`。

本阶段的结论只适用于表中明确列出的组合；它不代表任意 Modelica 模型、其他 OMC
版本、Linux/macOS、FMI 3 编译或 Model Exchange 编译已获验证。

状态含义：

| 状态 | 含义 |
|---|---|
| PASS | 已在列出的真实环境中完成编译、结构检查、Farcel 加载和运行；需要时还完成数值/导出验收。 |
| FAIL | 已执行但在所述阶段失败；应保留最小复现和错误证据。 |
| UNSUPPORTED | 当前 Farcel 产品契约明确不提供该组合，且在启动 OMC 前稳定拒绝。 |
| NOT_TESTED | 没有实际运行，不能从相邻组合推断结果。 |
| BLOCKED | 应测试但受明确外部条件阻塞，尚无可归类的执行结果。 |

## 2. 实测环境

| 维度 | 实测值 | 状态 |
|---|---|---|
| 操作系统 | Windows NT `10.0.26200`，x64 | PASS |
| Python | `3.13.15`，64-bit | PASS |
| FMPy | `0.3.31` | PASS |
| OpenModelica | `1.26.3`，通过 `D:\OpenModelica\bin\omc.exe` 显式选择 | PASS |
| FMI 编译产物 | FMI `2.0`、Co-Simulation | PASS |
| FMU 原生二进制 | `binaries/win64/<modelIdentifier>.dll` | PASS |
| OpenModelica 其他版本 | 未执行 | NOT_TESTED |
| Linux / macOS | 未执行 | NOT_TESTED |
| Modelica → FMI 3 编译 | 请求验证明确拒绝 | UNSUPPORTED |
| Modelica → Model Exchange 编译 | 请求验证明确拒绝 | UNSUPPORTED |

编译器路径也可通过 `FARCEL_OPENMODELICA_OMC` 指定；真实测试会先使用公开
`get_modelica_environment()` 确认该显式路径可用且版本为 `1.26.3`。未启用
`FARCEL_RUN_OPENMODELICA_INTEGRATION=1` 时，真实测试明确跳过；普通用户、默认
unittest 和 GitHub CI 不会自动启动或安装 OMC。

## 3. 按模型的真实结果

| 源码模型 | 类别与接口 | OMC 编译 / FMU 结构 / Farcel 加载 | 运行与数值验收 | CSV | 状态 / 证据 |
|---|---|---|---|---|---|
| `FarcelFirstOrder.mo` | 一阶动态；`T` parameter、`u` input、`y` output | FMI 2.0 CS，Windows x64 DLL；已加载 | `T=0.5,u=1` 最大误差 `0.003709761`；`T=1,u=1` 为 `0.001847100`；`T=.5,u=2` 为 `0.007419522` | PASS | PASS；Phase 9.4 真实边界测试 |
| `FarcelSecondOrder.mo` | 两个串联一阶环节；`T` parameter、`u` input、`x/y` 连续状态、`y` output | FMI 2.0 CS，`modelDescription.xml`、有效 model identifier 与 `binaries/win64/*.dll` 均已检查；已加载 | `T=.5,u=1` 最大误差 `0.003115566`；`T=1,u=1` 为 `0.001360165`；同一 FMU 的 `T=.5` 在 `t=.5` 输出 `0.264228606`，大于 `T=1` 的 `0.089435313` | 201 行；表头、时间和 `y` 数值均与 result 对照 | PASS；新增真实矩阵测试 |
| `FarcelAlgebraicGain.mo` | 无连续状态的直接传递；`K` parameter、`u` input、`y` output | FMI 2.0 CS，`modelDescription.xml`、有效 model identifier 与 `binaries/win64/*.dll` 均已检查；已加载 | `K=2,u=1.5` 的所有 201 个样本均为 `y=3`；同一 FMU 的 `K=3,u=2` 的所有样本均为 `y=6` | 本阶段不要求；动态模型已覆盖导出 | PASS；新增真实矩阵测试 |

二阶模型的解析基准为：

```text
y(t) = u * (1 - (1 + t/T) * exp(-t/T))
```

所有运行使用 0–2 s、`communication_step=0.01`，并要求 `COMPLETED`、201 个样本、
时间严格递增、输出长度匹配且所有时间/输出有限。两项新增模型在每次成功编译后均用
实际文件重算 SHA-256，并与 `ModelicaCompileResult.sha256` 比较；同一 FMU 以不同
参数和输入运行后哈希不变。FMU 内容会受到 OMC 构建时间等影响，故这里不把一次临时
构建的具体哈希误写为跨构建稳定的产品承诺。

## 4. 可重复执行

在安装 OpenModelica 1.26.3 的 Windows x64 主机上运行：

```powershell
$env:FARCEL_RUN_OPENMODELICA_INTEGRATION = "1"
$env:FARCEL_OPENMODELICA_OMC = "D:\OpenModelica\bin\omc.exe"

python -m unittest tests.integration.test_modelica_compatibility_matrix_real -v
```

真实测试使用临时目录保存 FMU 和 CSV，并在退出时清理；仓库不包含临时 FMU、CSV、
OMC 缓存或 DLL。测试只通过 `create_backend()` 的公开 API 执行，不调用 OMC
subprocess、FMPy 原生执行器或内部 FMU session。

无 OMC 的安全门由现有测试共同覆盖：`test_modelica_compatibility_matrix_real` 在未启用
环境开关时明确跳过；`test_modelica_compile_public_api` 验证普通 `create_backend()`
仍可加载既有 FMU、不会自动编译，且无效编译请求返回稳定 `CONFIG_ERROR`。原有
FMU runtime、Graph/Worker 和 Project schema 均未修改。

## 5. 已知限制与 9.5B 建议

本矩阵只覆盖三个无外部库、单文件的模型和当前 Windows x64 环境。没有观察到二阶
或纯代数模型在 OMC 编译、FMU 结构、Farcel 初始化或执行阶段的兼容性问题；这不构成
对复杂事件、数组、多文件 Package、FMI 3 或 Model Exchange 的支持声明。

本段保留的是 Phase 9.5A 当时的后续建议记录：若后续单独批准 Phase 9.5B，可在不修改默认 CI、也不自动安装 OMC 的前提下，提供
一个手动触发或自托管 Windows OMC 1.26.3 的可选 workflow：设置上述两个环境变量，
运行本矩阵和既有真实测试，并把 OMC 版本、平台和 unittest 输出作为 artifact。该建议
已由 Phase 9.5B 实施；实际 CI 证据见第 6 节。

## 6. Phase 9.5B 的独立 GitHub CI 证据

上述建议已在独立的可选 workflow 中实现；默认 backend CI 仍不安装 OMC。带
`[omc-ci]` 的验证在
[GitHub Actions run #40473315209](https://github.com/horafeng/Farcel/actions/runs/40473315209)
真实执行成功：Windows NT `10.0.26100.0` x64、Python `3.13.16`、FMPy `0.3.31`、
OpenModelica `1.26.3`，实际探测到的路径为
`C:\Program Files\OpenModelica1.26.3-64bit\bin\omc.exe`。该 Job 运行 31 个登记
测试（16 个受 OMC 门控的真实用例和 15 个同模块安全用例），全部通过且 0 skipped；
它包含本矩阵的二阶动态/纯代数模型，以及一阶、参数时序、Project、公共 API 和 CLI
覆盖。诊断 artifact 仅包含环境摘要和 unittest 日志，不包含 FMU、DLL 或 OMC 安装目录。
