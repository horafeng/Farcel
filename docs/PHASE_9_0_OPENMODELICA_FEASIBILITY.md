# Phase 9.0：OpenModelica 可行性预验证与架构冻结

## 1. 结论与开发基线

本记录冻结 Phase 9.0 的技术边界，不实现 Modelica 的生产接口、编译器
Adapter 或 GUI。开发从 `origin/main` 的
`de8647e00e31ef4b693e0e44d03b9671f039bfd8`（Phase 8 merge）开始，工作分支为
`phase-9-modelica-integration`。

代码核查结果如下：

- 当前公开单 FMU 执行范围是 FMI 2.0 Co-Simulation、FMI 2.0 Model Exchange
  及 FMI 3.0 Co-Simulation；FMI 3.0 Model Exchange 与 Scheduled Execution
  仅可 inspect。
- `create_backend()` 在 `src/farcel/backend.py` 组合 `FarcelEngine`、FMPy
  importer、FMI session/solver factories 与 CSV exporter。FMPy 仍只存在于
  `infrastructure/fmpy/`。
- 公共工作流是 `load_fmu(path)`、`validate_config(metadata, config)`、
  `run_fmu(path, config)` 与 `export_result(result, destination)`。
  `run_fmu()` 会自行再次 load/validate 并选择执行 runner。
- `SimulationConfig.initial_inputs` 在初始化时写入 input；`parameters`
  提供参数覆盖；`selected_outputs` 决定 canonical result/CSV 的读取列。
- importer 将“可解析”与“可执行”分开：`ModelMetadata.interface_types` 表示
  描述文件接口，`executable_interface` / `capabilities.can_execute` 还要求
  当前平台二进制、受支持接口和不需要外部 execution tool。FMI 2 双接口优先
  Co-Simulation。

因此 Modelica 的合理路径是让编译器只产出普通 FMU，继续由既有 public FMU
workflow 执行；不需要、也不得重写 FMPy runtime、CVode 或 SimulationGraph。

## 2. 当前环境记录

本次执行环境为 Windows NT `10.0.26200.0`、`AMD64`（.NET 报告为 Windows
10.0.26200），Python `3.13.15`（64-bit），FMPy `0.3.31`。`gcc` 和 `cmake`
可在 PATH 中发现，但它们不能替代 OpenModelica 的 FMU build toolchain。

预检结果：

- `OPENMODELICAHOME` 未设置；
- PATH 中没有 `omc`；
- `C:/OpenModelica*/bin/omc.exe` 与 `C:/Program Files/OpenModelica*/bin/omc.exe`
  未发现安装；
- 因此没有可报告的 OMC 版本、不能实际调用 `omc --version`，也不能确认
  OMC 所需的当前平台 native FMU 编译工具链。

这不是编译成功；在安装 OpenModelica 前，Phase 9.0 的真实编译与 native
FMU 运行验收均为未执行。建议从 [OpenModelica 官方网站](https://openmodelica.org/)
安装与当前 Windows 平台相匹配的版本，重新打开终端后运行本阶段脚本。工具不在
代码中写死用户路径：优先接受 `--omc`，其次 `OPENMODELICAHOME/bin/omc`、PATH，
最后只在 Windows 探测常见安装根目录。

## 3. 最小模型与可重复验证

`examples/modelica/FarcelFirstOrder.mo` 是无第三方依赖的最小一阶惯性模型：

```modelica
model FarcelFirstOrder
  parameter Real T(min=1e-9) = 0.5;
  input Real u;
  output Real y(start=0, fixed=true);
equation
  der(y) = (u - y) / T;
end FarcelFirstOrder;
```

开发验证工具为 `tools/modelica/validate_openmodelica_fmu.py`。默认把临时
`.mos`、FMU 和 CSV 写入已忽略的 `artifacts/modelica/`，不提交二进制 FMU。
命令如下：

```powershell
python .\tools\modelica\validate_openmodelica_fmu.py
# 或显式选择安装位置和工作目录
python .\tools\modelica\validate_openmodelica_fmu.py --omc "C:\OpenModelica\bin\omc.exe" --work-dir .\artifacts\modelica
```

工具先真实执行 `omc --version`，再生成并执行 OMC script。脚本明确依次调用
`loadFile()`、`checkModel()`、`buildModelFMU(FarcelFirstOrder,
version="2.0", fmuType="cs")` 和 `getErrorString()`；`fmuType="cs"` 是明确
指定而不是依赖 OMC 默认值。进程有可配置的 `--timeout-seconds`；超时会由
`subprocess.run()` 终止并返回失败。脚本保留 stdout/stderr、返回码与
`getErrorString()` 标记日志，非零返回码、`loadFile()!=true`、未报告 FMU 或
产物不存在均为失败。

成功后工具检查 FMU ZIP 中 `modelDescription.xml`、FMI version `2.0` 与
`CoSimulation` XML 接口，并仅通过 Farcel public API 执行：

```python
backend = create_backend()
metadata = backend.load_fmu(fmu_path)
backend.validate_config(metadata, config)
result = backend.run_fmu(fmu_path, config)
backend.export_result(result, csv_path)
```

配置为 `u=1`、`T=0.5`、0–2 s、步长 0.01、输出 `y`。工具要求时间轴从 0 到
2 严格递增、所有 `y` 有限，并按 `y(t)=1-exp(-t/0.5)` 比较 0、0.5、1、2 s；
最大绝对误差不得超过 `0.03`。CSV 的行数也必须等于 result 样本数。当前因 OMC
缺失，这些数值、FMU 结构及 CSV 实测记录均为“未执行”，不能填写或推断数值。

## 4. 测试与环境隔离

`tests/integration/test_openmodelica_feasibility.py` 中的普通测试不需要
OpenModelica：它验证模型接口、缺失 OMC 返回 2 且不宣称成功、以及 OMC 命令失败
返回非零。真实集成测试只有在下列环境变量显式设为 `1` 时才运行：

```powershell
$env:FARCEL_RUN_OPENMODELICA_INTEGRATION = "1"
python -m unittest tests.integration.test_openmodelica_feasibility
```

没有该变量时显示 `skipped`，这只表示本机没有请求真实集成验证，绝不表示 OMC
兼容性通过。OpenModelica 不是 `pyproject.toml` 的 Python dependency，因此普通
FMU 用户和 CI 不会被强制安装它。

本机完整既有测试在当前受限执行器中会因 Python/FMPy 临时解压目录的 `WinError 5`
失败；即使改到仓库 `artifacts/test-tmp`，该执行器仍拒绝 tempfile 清理。该问题
早于本阶段代码且影响既有 native FMU 测试，不能据此判断 Phase 9.0 引入回归。
新增不依赖 native FMU 的测试可单独运行。

## 5. 冻结的目标架构

后续 production implementation 必须保持如下依赖方向：

```text
GUI / CLI
    ↓
FarcelEngine
    ↓
ModelicaService
    ↓
ModelicaCompiler Port
    ↓
OpenModelica Adapter
    ↓
OMC → FMU
    ↓
Existing FMPy Runtime
```

本图是接口草案，不代表 Phase 9.0 已创建其中任何 production 类型。`ModelicaService`
只编排 source、编译请求、diagnostic 与 FMU artifact；它不解析 Modelica 方程，不
创建第二个 simulator，也不调用 FMPy implementation object。编译完成的 `.fmu`
仍通过现有 `load_fmu` / `validate_config` / `run_fmu`，从而维持
`GUI / CLI → application → contracts ← infrastructure`。

未来公共 Modelica DTO 的最小候选字段是：source model 的相对路径、class name、
compiler identity/version、FMI version、FMU type、target platform、working/artifact
目录标识、编译状态、稳定 diagnostic entries 与产物相对路径/哈希。它们不得包含
OMC session、subprocess handle、绝对用户路径、FMPy description 或 native library
handle。当前 Phase 9.0 不定义或发布这些 DTO。

OMC adapter 应以显式 configured/discovered executable 启动受限子进程、设置明确
working directory、保存 stdout/stderr 与 `getErrorString()`，并设置可配置 timeout。
超时后应终止进程及其可控子进程、保留诊断但清理临时目录。未来 application facade
必须把找不到 compiler、启动失败、超时、OMC nonzero、无 FMU 产物与 FMU import
失败映射为 Farcel `EngineError` 的稳定 code/message/details；UI 不能解析原始
OMC 日志决定状态。

source 与 artifact 的关联应在 project/asset 管理层记录为可重建的 source snapshot、
compiler request、版本、平台、artifact hash 与相对 artifact path。现有 Project
schema 本阶段不变；Phase 9.1 前必须先专门设计其演进和迁移，而不是把绝对 OMC
路径或临时目录塞入当前 schema。

## 6. 后续阶段边界与职责

| 阶段 | 允许范围 | 明确不包含 |
|---|---|---|
| 9.1 | Farcel-owned compiler port、最小 DTO/错误模型、application `ModelicaService` 与 OpenModelica infrastructure adapter 的设计/实现。 | Project schema 持久化、GUI、Modelica parser。 |
| 9.2 | source-to-FMU artifact 关联、受控的 local artifact lifecycle 与 project schema 演进。 | 云端编译、自动安装编译器。 |
| 9.3 | CLI 的显式 compile/diagnostic workflow。 | GUI 编译体验。 |
| 9.4 | 参数/编译选项与现有 FMU parameter override 的边界验证。 | 改写现有参数系统。 |
| 9.5 | 真实 OMC/Farcel compatibility matrix 与 CI 可选集成门。 | 默认 CI 安装 OMC。 |
| 9.6 | GUI 在稳定 public API 上的 source/artifact presentation。 | GUI 直接调用 OMC/FMPy。 |
| 9.7 | 仅经独立设计批准后的可复现性、缓存与清理强化。 | 网络 compiler service。 |
| 9.8 | release hardening、文档与支持矩阵。 | Modelica 语言全量解析或第二套 runtime。 |

前端职责是选择 source/compile configuration、调用未来 public facade、展示 Farcel
DTO 与诊断、以及用现有 result API 绘图。后端职责是 compiler discovery、进程
生命周期、产物验证、错误映射与复用 existing FMU runtime。前端不得 import
infrastructure、FMPy 或调用 OMC。

## 7. 未解决事项与 Phase 9.0 状态

待安装 OpenModelica 后必须重新执行：`omc --version`、`loadFile`、`checkModel`、
FMI 2.0 CS `buildModelFMU`、FMU binary/current-platform executable policy、真实
Farcel native simulation、数值误差和 CSV。还需要记录 OMC release、编译器日志、
FMU platform folder 与 `T=1.0` 参数覆盖的实际结果。

**Phase 9.0 状态：BLOCKED。** 原因是当前机器没有 OpenModelica，因此不能满足真实
OMC 编译、FMU native execution 与数值验收条件；文档、模型与测试脚手架不构成通过。
