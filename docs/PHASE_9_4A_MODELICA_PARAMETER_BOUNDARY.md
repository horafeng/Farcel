# Phase 9.4A：Modelica 参数与编译选项边界验证

## 1. 结论

Phase 9.4A 不增加新的参数或编译 API。它确认现有 Farcel 契约已经把 Modelica
源码、FMU 初始化参数、FMU 输入和编译选项分开：编译只生成普通 FMI 2.0
Co-Simulation FMU；运行时仍通过既有 `SimulationConfig` 和 FMI Runtime 操作该 FMU。

`FarcelFirstOrder.mo` 中的 `parameter Real T(min=1e-9) = 0.5` 是 Modelica
源码声明。经 OpenModelica 编译后，FMU metadata 将 `T` 公开为 causality
`parameter`，因此可以在每次仿真初始化前用 `SimulationConfig.parameters` 覆盖。
它不是运行过程中的 input，也不能据此宣称可实时调节。

## 2. 参数能力矩阵

| 类别 | Farcel 判断依据 | 设置时机 | 是否需要重新编译 | 前端推荐操作 | 当前状态 |
|---|---|---|---|---|---|
| `.mo` 源码内容（含默认 `parameter` 值、方程） | 源文件内容与编译请求 | 编译前 | 是，生成新的 FMU | 源码编辑后重新编译/登记新资产 | 已实现 |
| FMU parameter | metadata `causality=parameter`（FMI 3 另有受限 `structuralParameter`） | FMU 初始化前 | 通常不需要；取决于该 FMU 是否实际公开该变量 | 模块参数面板，写入 `SimulationConfig.parameters` | 已实现并有验证 |
| FMU input | metadata `causality=input` | 初始化前或允许的通信点 | 否 | 初值/输入连接写入 `initial_inputs`；时序变化写入 `input_schedule` | 已实现并有验证 |
| FMI/FMU 编译类型 | `ModelicaCompileRequest.fmi_version`、`fmu_type` | 编译请求创建时 | 是，属于编译配置 | 只展示 FMI 2.0 和 Co-Simulation | 已实现且严格受限 |
| 输出采样设置 | `communication_step`、`output_interval` | 仿真配置时 | 否 | 仿真设置面板 | 已实现 |

不得按变量名称判断类别。前端必须读取 `VariableMetadata.causality`、
`variability`、类型、范围与现有 `validate_config()` 结果；不能把 state、output 或
普通 local variable 显示为可写参数。

## 3. 运行时边界

### 3.1 源码参数与 FMU 参数

修改 `.mo` 的 `T` 默认值或方程会改变编译输入，必须重新编译，并生成新的 FMU
和新的 Project `ModelAsset`/`ModelicaBuildProvenance` 记录。已登记构建记录包含
不可变 source snapshot SHA-256、编译器版本、FMI 版本、FMU 类型和资产标识。

对同一个已编译 FMU 使用 `parameters={"T": 0.5}` 或 `{"T": 1.0}` 不重新调用
OpenModelica，不会改写 `.mo`，也不会改写 FMU 文件或其 SHA-256。FMI 2 runtime 在
初始化序列中设置这些值；无效名称、causality、类型和范围均由现有验证器在启动
runtime 前拒绝。具体 FMU 是否允许某个参数覆盖仍以其导出的 FMI metadata 和
runtime 行为为准。

### 3.2 输入与参数

`initial_inputs={"u": 1.0}` 只接受 causality `input`，与 `parameters` 是不同字段。
它在初始化时设置，不会变更 `T` 等 parameter。`input_schedule` 是运行期间的离散
input 更新：每项必须是 `InputUpdate`，时间严格递增、位于 `[start_time, stop_time)`，
且与 communication point 对齐；值保持至下一次更新。它不支持在运行时写 parameter、
state 或 output，也不等同于连续输入插值或通用实时调参。

### 3.3 编译选项

公开 `ModelicaCompileRequest` 字段保持不变：`source_path`、`model_name`、
`output_directory`、可选 `compiler_path`、`fmi_version`、`fmu_type` 和
`timeout_seconds`。当前只接受 `fmi_version="2.0"` 和 `fmu_type="cs"`；FMI 3.0、
Model Exchange 和其他 OMC 选项均在启动 OMC 前以字段级 `CONFIG_ERROR` 拒绝，绝不
降级或偷偷回退为 FMI 2.0 CS。`timeout_seconds` 必须为大于零且不超过 1800 的有限数值；
模型名必须是 Farcel 当前允许的点分标识符。

## 4. Project provenance

Project schema 1.1 在编译登记时才新增一个 `ModelAsset` 与一个对应的
`ModelicaBuildProvenance`。运行时 `parameters`/`initial_inputs` 仅是本次
`SimulationConfig`，不会改变上述记录、source snapshot 或 FMU SHA-256，也不会新增
Modelica 构建记录。schema 1.0 不会自动登记，必须先走既有显式迁移。

## 5. 真实验证

新增的 opt-in 集成测试 `tests/integration/test_modelica_parameter_boundary_real.py` 使用
`D:\OpenModelica\bin\omc.exe`（由 `FARCEL_OPENMODELICA_OMC` 覆盖）和同一份
`FarcelFirstOrder.mo`。它验证：

1. 单个编译 FMU 分别以 `T=0.5`、`T=1.0` 运行，均与
   `y(t)=1-exp(-t/T)` 比较，最大绝对误差不超过 `0.03`，且 `T=0.5` 响应更快；
2. 固定 `T=0.5`，`u=1.0`、`u=2.0` 分别符合
   `y(t)=u(1-exp(-t/T))`，证明参数与初始化输入走不同入口；
3. FMI 3.0 与 Model Exchange 编译请求在 OMC 启动前得到带字段 issues 的
   `CONFIG_ERROR`；
4. Project 1.1 登记后，以同一 asset 的 `T=0.5`、`T=1.0` 运行不会改变 provenance、
   `ModelAsset`、FMU SHA-256 或构建记录数量。

本次验证使用 OpenModelica `1.26.3`。一次临时编译所得 FMU SHA-256 为
`5111de901d2870901e25a585dc20615b7c5599be8ab6fca5f269f75cf4811cd5`；同一
FMU 连续运行时未发生变化，原始 `FarcelFirstOrder.mo` 的 SHA-256 也保持
`259c7e20c001e593a7b19153ace4bcd31935660fb783deedf278cf21cee5d7a9`。该 FMU
的内容哈希是本次生成产物的记录，不承诺不同 OMC 构建时间或版本必然相同。

| T | u | 最大绝对误差 | y(0.5) | y(2.0) |
|---:|---:|---:|---:|---:|
| 0.5 | 1.0 | 0.003709761 | 0.635830320 | 0.982412053 |
| 1.0 | 1.0 | 0.001847100 | 0.394993933 | 0.866020325 |
| 0.5 | 2.0 | 0.007419522 | 1.271660640 | 1.964824107 |

前两行证明 `T=0.5` 比 `T=1.0` 响应更快；第一、三行显示输入幅值翻倍时输出
相应翻倍。普通单元测试覆盖编译请求字段、不支持选项、非法超时/模型名以及
parameter/input causality 分界，不安装或启动 OMC。
已有 `test_modelica_compile_public_api.py` 继续覆盖未配置/未调用 OMC 时普通 FMU
导入路径可用，避免重复同一断言。

运行真实测试：

```powershell
$env:FARCEL_RUN_OPENMODELICA_INTEGRATION = "1"
$env:FARCEL_OPENMODELICA_OMC = "D:\OpenModelica\bin\omc.exe"
python -m unittest tests.integration.test_modelica_parameter_boundary_real -v
```

## 6. 前端控制建议

1. 仅把 metadata 表明为可覆盖 parameter 的变量显示在模块参数面板；编辑结果进入
   `SimulationConfig.parameters`，每次运行前重新初始化 FMU。
2. 把 input 初值与运行中计划输入作为仿真配置/输入连接呈现，分别使用
   `initial_inputs` 与 `input_schedule`；后者只提供通信点上的离散变更。
3. 用户编辑 `.mo`、默认参数或编译形状时，提示重新编译并作为新资产登记；不要把它
   伪装成运行时参数覆盖。
4. 输出采样属于仿真设置，不能显示为 Modelica/FMU parameter。
5. 不显示 FMI 3 编译、Model Exchange 编译、任意 OMC 自定义选项、运行中 parameter
   写入、连续输入插值或 GUI 自动重编译为可用功能。

## 7. 已知限制与 Phase 9.4B 候选事项

当前结论只覆盖 OpenModelica 1.26.3、Windows x64、`FarcelFirstOrder`、FMI 2.0 CS
和当前 FMPy runtime；不表示所有 Modelica parameter 都能被每个 FMU 覆盖。Farcel
没有 Modelica 源码参数解析器，也不会仅因源码出现 `parameter` 就承诺它可编辑。

如 Phase 9.4B 获授权，可仅评估并修复真实验证发现的最小缺口，例如：向前端提供
稳定的 metadata-to-control 映射展示、明确 FMI parameter 初始性限制的诊断，或为
合法的特定 FMU parameter override 改进错误信息。本阶段不实施这些后续工作。
