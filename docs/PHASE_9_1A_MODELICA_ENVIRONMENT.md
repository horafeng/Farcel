# Phase 9.1A：OpenModelica 编译器环境检测与公共接口

## 1. 目标与范围

Phase 9.1A 让 GUI、CLI 和 Python 调用方通过 Farcel 自身的公共 backend 查询本机
OpenModelica 是否可用。该能力只检测环境，不编译 `.mo` 文件、不创建 FMU，也不改变
既有 FMI、Graph、Project 或 Worker 行为。

Phase 9.0 已验证 `FarcelFirstOrder.mo` 在当前 Windows x64 主机上可由 OpenModelica
1.26.3 编译为 FMI 2.0 Co-Simulation FMU；Phase 9.1A 不把那条验证脚本变成 production
编译接口。

## 2. 公共合同与状态语义

`farcel.contracts` 导出不可变的 `ModelicaEnvironmentInfo`：

- `status: ModelicaEnvironmentStatus`：`AVAILABLE`、`NOT_FOUND` 或 `UNUSABLE`；
- `compiler_name: str`：当前实现为 `OpenModelica`；
- `compiler_version: str | None`：仅在成功执行并识别版本时提供；
- `diagnostic_message: str`：可直接显示的简体中文诊断；
- `is_available`：只由 `status is AVAILABLE` 推导，避免重复状态。

`NOT_FOUND` 表示没有候选编译器，或调用方提供的显式路径不存在。`UNUSABLE` 表示发现或
指定了候选文件，但不能启动、超时、返回非零，或未输出可识别的 OpenModelica 版本。
`AVAILABLE` 仅在真实 `omc --version` 返回成功且输出版本后返回。

这些类型仅依赖 Python 标准库和 Farcel contract；它们不包含 subprocess handle、OMC
session、FMPy 对象或需要持久化的用户绝对路径。

## 3. 依赖方向与 Port

`ModelicaEnvironmentDetector` 是 contracts 中的最小 Protocol，定义：

```python
detect(compiler_path: str | Path | None = None) -> ModelicaEnvironmentInfo
```

`ModelicaService` 位于 application，只把调用委托给该 Port。OpenModelica 的目录发现、
subprocess 和版本解析全部位于
`farcel.infrastructure.openmodelica.OpenModelicaEnvironmentDetector`。application、
contracts 与 GUI 都不直接导入或调用 subprocess、OMC 或 FMPy。

## 4. 发现与验证规则

未提供路径时，适配器按以下顺序寻找候选项：

1. `OPENMODELICAHOME/bin/omc.exe`（非 Windows 使用 `omc`）；
2. PATH 中的 `omc.exe` / `omc`；
3. Windows 常见的 `C:\OpenModelica*` 和
   `C:\Program Files\OpenModelica*` 安装目录。

调用方提供 `compiler_path` 时，该路径是严格选择：文件不存在立即返回 `NOT_FOUND`，绝不
回退到环境变量、PATH 或默认目录。production 代码不硬编码开发机的 `D:\OpenModelica`
路径。

适配器只执行一次 `[omc, "--version"]`，使用 `shell=False`、捕获输出和默认 10 秒超时。
它不修改 PATH、不自动安装软件，也不会运行编译或其他 OMC 命令。

## 5. 公共 API 示例

```python
from farcel import create_backend

backend = create_backend()
environment = backend.get_modelica_environment(
    compiler_path="D:/OpenModelica/bin/omc.exe"
)

if environment.is_available:
    print(environment.compiler_version)
else:
    print(environment.diagnostic_message)
```

没有 OpenModelica 时，`create_backend()` 和既有 FMU 工作流仍可正常使用；环境查询返回
`NOT_FOUND`，不会安装软件。对于手动组装但未注入 `ModelicaService` 的
`FarcelEngine`，查询返回稳定的 `UNUSABLE` 诊断，而不是 AttributeError。

## 6. 验证范围

单元测试模拟路径发现、严格显式路径、缺失候选、启动失败、非零退出、超时、空/无效版本
以及有效版本。application 测试验证 Service 与 Engine 的委托及未注入行为。公共 API
测试验证默认 backend 的无效显式路径；真实 OMC 测试仅在
`FARCEL_RUN_OPENMODELICA_INTEGRATION=1` 时执行，在未安装 OMC 的 CI 中明确跳过。

本阶段完成后还会重跑 Phase 9.0 真实编译/FMU 回归与完整后端回归。GitHub CI 继续使用
Windows Python 3.10 / 3.13 矩阵，不安装 OpenModelica，因而真实检测测试保持可选。

## 7. Phase 9.1B 预定边界

Phase 9.1B 如获单独授权，才可设计 Farcel-owned 的编译请求、稳定诊断和最小 compiler
port。它仍不能绕过 application/contracts 边界，也不会由本阶段自动开始。

Phase 9.1B 已在获得单独授权后完成其合同与纯请求验证基础；详情见
`PHASE_9_1B_MODELICA_COMPILATION_CONTRACTS.md`。它仍不实现真实编译。

## 8. 本机验证记录（2026-10-20）

本机通过显式路径 `D:\OpenModelica\bin\omc.exe` 调用
`create_backend().get_modelica_environment()`，实际返回：

```text
status=AVAILABLE
compiler_name=OpenModelica
compiler_version=1.26.3
diagnostic_message=OpenModelica 编译器可用。
```

真实环境检测集成测试在 `FARCEL_RUN_OPENMODELICA_INTEGRATION=1` 时为 `2/2` 通过。
它实际执行 `omc.exe --version`；不安装 OMC 的 CI 仍会明确跳过该可选测试。

作为回归验证，Phase 9.0 的
`tools/modelica/validate_openmodelica_fmu.py --omc D:\OpenModelica\bin\omc.exe`
也再次成功：`FarcelFirstOrder.mo` 生成 FMI 2.0 Co-Simulation FMU，Farcel 对
`T=0.5` 和 `T=1.0` 的最大绝对误差分别为 `0.003709761` 与 `0.001847100`，两份 CSV
各有 201 行。

完整后端测试按 contracts（10）、application（49）、unit（556）和 integration（136）
执行，总计 `751`：`748` 通过、`3` 个既有跳过、`0` 失败、`0` 错误。integration 组包含
真实 OMC 环境检测和真实 Phase 9.0 FMU 测试，未使用 bypass。
