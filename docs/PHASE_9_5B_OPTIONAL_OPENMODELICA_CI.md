# Phase 9.5B：可选真实 OpenModelica CI

## 1. 目的与边界

`.github/workflows/backend-ci.yml` 是默认后端 CI：它继续在 Windows 上运行 Python
3.10 和 3.13，不安装 OpenModelica，也不设置真实 Modelica 集成的环境变量。它保证
普通开发者和普通 CI 使用者不必安装 OMC。

`.github/workflows/modelica-omc-optional.yml` 是独立的真实兼容性门。它只在 Windows
x64、Python 3.13、OpenModelica 1.26.3 上验证 Modelica → FMI 2.0 Co-Simulation FMU
→ Farcel 的既有公共路径；没有修改 FMU runtime、编译公共 API、Project schema、
Graph 或 Worker。

## 2. 触发方式

新 workflow 具有两个触发器：

1. `workflow_dispatch`：workflow 已存在于目标分支后可从 Actions 页面手动运行；
2. 推送到 `phase-9-4-parameter-boundary`：仅当本次提交消息包含 `[omc-ci]` 时，真实
   Job 才运行。

当前阶段使用第二种机制完成首次验证，因为新 workflow 在默认分支尚不存在，不能假定
它的 `workflow_dispatch` 按钮已经可用。普通开发分支 push 会创建 workflow run，但
真实 Job 显示 `skipped`，表示没有请求 OMC 验证，绝不表示 OMC 兼容性通过。

合并到 `main` 后（本阶段不执行合并）可使用 `workflow_dispatch` 重新运行相同门禁。

## 3. 安装与环境验证

工作流使用 OpenModelica 官方的 `OpenModelica/setup-openmodelica`，固定到其 v1.0.7
的 commit `d7191bc0249c0c9a012cb8e16a43ae654d8bc26b`，传入：

```yaml
version: "1.26.3"
architecture: "64"
```

该官方 action 支持 Windows x64 的 1.26.3 安装。安装后，工作流从 `PATH` 的实际
`Get-Command omc` 结果取得路径，运行 `omc --version`，要求退出码为零且输出严格
匹配 1.26.3；它还确认 runner 是 x64。只有成功后才经 `$GITHUB_ENV` 写入：

```text
FARCEL_RUN_OPENMODELICA_INTEGRATION=1
FARCEL_OPENMODELICA_OMC=<实际探测到的 omc.exe>
```

因此没有使用开发机的 `D:\OpenModelica\bin\omc.exe`，也不存在 Fake Compiler 或
版本回退。Job 设为 `contents: read`、45 分钟超时；不缓存未校验的编译器，也不上传
FMU、DLL 或整个 OMC 安装目录。

## 4. 真实测试清单与防 skip 门禁

真实 Job 只运行下面登记模块，而不是依赖一次完整 unittest 的偶然 skip 行为：

| 模块 | 受 OMC 门控的实际用例数 | 覆盖 |
|---|---:|---|
| `test_openmodelica_feasibility` | 1 | OMC 可行性、公共 API 编译/运行 |
| `test_openmodelica_compiler` | 1 | Adapter 真实编译、FMU 运行 |
| `test_modelica_environment_public_api` | 1 | public 环境检测 |
| `test_modelica_compile_public_api` | 2 | 公共编译、导出、Project 编译/迁移 |
| `test_modelica_asset_staging` | 1 | snapshot、登记、重定位 |
| `test_modelica_cli_real` | 1 | 安装后的 CLI 编译/检查/运行 |
| `test_modelica_project_cli_real` | 1 | Project CLI 迁移、编译、来源追溯 |
| `test_modelica_parameter_boundary_real` | 6 | parameter/input、schedule 一步时序、Project 不变性 |
| `test_modelica_compatibility_matrix_real` | 2 | 二阶动态、纯代数增益、CSV |
| **总计** | **16** | **真实 OMC 用例** |

这些模块还包含 15 个不依赖 OMC 的安全/契约用例，所以执行报告必须严格为 **31 passed,
0 skipped**。工作流读取 verbose unittest 日志并验证：退出码、无 `skipped`、全部 16
个真实测试 ID 都为 `ok`、`Ran 31 tests` 以及恰好 31 个 `ok`。因此所有 OMC 用例被
条件跳过时不会得到绿色结果。

安装验证、unittest 日志和 Windows/Python/FMPy/OMC 摘要会以
`modelica-omc-ci-diagnostics` artifact 保留 7 天（无论成功或失败）。这为安装、FMU
结构/编译、Farcel 运行、数值断言和 Project/CLI 失败提供定位证据，不保存大型 FMU
或 OMC 安装产物。

## 5. 实际 CI 证据

首次配置验证在
[run #40470064065](https://github.com/horafeng/Farcel/actions/runs/40470064065) 成功；
完整环境回显与同一门禁的复验在
[run #40471491499](https://github.com/horafeng/Farcel/actions/runs/40471491499) 成功。
后者运行的提交为 `0215b6f8ad742f71770eebc6ab1f31bcf5239f03`，实际记录如下：

| 项目 | 真实 GitHub Runner 结果 |
|---|---|
| 操作系统 | `Microsoft Windows NT 10.0.26100.0`，x64 |
| Python | `3.13.16` |
| OpenModelica | `OpenModelica v1.26.3 (64-bit)` |
| 实际 `omc.exe` | `C:\Program Files\OpenModelica1.26.3-64bit\bin\omc.exe` |
| FMPy | `0.3.31` |
| 测试汇总 | 31 passed，0 skipped，耗时 315.650 s |
| 真实 OMC 用例 | 16/16 已由测试 ID 门禁逐项确认 `ok` |
| 安装 / 结构 / 运行 | 官方 action 安装、版本/x64/path 验证、真实 FMU 编译和 Farcel 运行均通过 |
| 诊断 artifact | `modelica-omc-ci-diagnostics`，保留 7 天 |

因此该记录不是本地开发机或默认 CI 的 skip 结果。测试包含一阶、二阶、纯代数模型，
FMI 2.0 CS 结构与 Windows x64 DLL、参数覆盖、input schedule 一步时序、CSV、Project
asset/provenance、公共 API 与安装后 CLI；数值阈值保持 Phase 9.4/9.5A 的既有断言。

## 6. 已知限制

- 兼容性声明只覆盖真实运行过的 OMC 1.26.3、Windows x64、Python 3.13 与现有 FMI
  2.0 Co-Simulation 编译范围；不保证任意 Modelica Package、多文件 Package、FMI 3
  编译或 Model Exchange 编译。
- GitHub Runner 性能与用户机器不同；OMC 安装可能较慢，网络或官方安装源故障必须令
  Job 失败，不能误报 PASS。
- 默认 backend CI 仍不要求 OMC。普通 push 的可选 Job skipped 只是未请求的状态。
