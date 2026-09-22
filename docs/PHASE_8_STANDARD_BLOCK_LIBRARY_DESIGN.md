# Phase 8 — Farcel Standard Block Library

## 状态、范围与冻结基线

**Phase 8.0 — architecture / design freeze。** 本文是 Farcel 标准仿真模块库
（Farcel Standard Block Library）的规范性设计记录，冻结自已验证的
`origin/main` 提交 `3b12846871994eaf52e23e65276df814c62807b0`
（`docs: finalize documentation consistency`）。

Phase 8 的目标是让 Farcel 从“必须导入用户外部 FMU 后才能构建
`SimulationGraph`”，演进为“后端自带可供前端浏览、拖拽和实例化的基础标准
模块库”。第一版标准模块最终仍以 FMU 执行，并进入既有图执行体系；它不是
Python 原生模块运行时，也不是第二套 graph runtime。

本 8.0 阶段只冻结设计、路线图和 `phase-8-work` 的 CI 触发。它不创建 Python
contract、catalog、`block.json`、FMU、资源加载器或模块运行代码。尤其不实现
PID、Gain、Step 或任何其他模块的数值执行。

## 1. 架构与依赖边界

后续 Phase 8 的唯一集成路径冻结为：

```text
GUI / CLI
  ↓
FarcelEngine
  ↓
Standard Block Catalog / application convenience layer
  ↓
Farcel-owned block descriptors
  ↓
packaged FMU asset
  ↓
existing ModelNode / ModelNodeConfig
  ↓
existing SimulationGraph
  ↓
existing GraphValidator
  ↓
existing GraphRuntimeBindingsFactory
  ↓
existing LOCAL / WORKER runtime
```

总体依赖方向保持不变：

```text
GUI / CLI
  ↓
application
  ↓
contracts
  ↑
infrastructure
```

`farcel.infrastructure.fmpy` 是 FMPy 的唯一边界；FMPy 对象、native FMU 对象
和实现细节不得穿过 Farcel public API。PySide6 前端不得依赖
`farcel.infrastructure`、FMPy、`WorkerRpcClient`、`RemoteNodeRuntime`、socket/TCP
或 native FMU object。前端继续只通过 `farcel.create_backend()` 和
`farcel.contracts` 使用后端。

标准模块 convenience layer 将描述符查找、打包资源解析与参数覆盖转换为既有
`ModelNode(node_id=..., model_path=..., config=ModelNodeConfig(parameters=...))`。
它不得引入 `StandardBlockNodeRuntime`、`PythonBlockRuntime`、`BlockGraph`、
`StandardSimulationGraph` 或任何平行执行体系。

## 2. Phase 8 v1 FMU 策略

第一版所有可执行标准模块统一使用 **FMI 2.0 Co-Simulation**。不在第一版混用
FMI 2 Model Exchange 或 FMI 3。这是因为 FMI 2 CS 是当前最成熟的执行路径，已
支持 `run_fmu`、`run_graph`、LOCAL/WORKER 运行和普通 FMU 的 Worker asset
staging；该选择最大化复用 Phase 1–7，并显著缩小首版测试矩阵。

v1 只支持 scalar `Real` 输入、输出和参数；不因标准库增加 arrays、dynamic
dimensions、binary、clock、Scheduled Execution 或 FMI 3 Model Exchange。每个可
执行模块都是独立 FMU，例如 `PID.fmu`、`Gain.fmu` 与 `Step.fmu`。

标准 FMU 必须拥有 Farcel 自有的构建源、明确来源和合法再分发权。仓库必须保留
构建源和构建说明，后续建立可重复执行的 build script；release/wheel 将携带已
构建 FMU。正常运行 Farcel 时，用户不得被要求安装 OpenModelica、MATLAB/
Simulink、AMESim、ANSYS 或编译器。禁止复制许可证不明确的第三方 FMU。本设计
冻结策略，不在 8.0 实际构建任何 FMU。

## 3. 目录和资源布局

后续资产位于 Farcel package 内，目标布局冻结如下（此阶段不创建这些文件）：

```text
src/farcel/standard_library/
    __init__.py
    assets/
        catalog.json
        sources/
            constant/Constant.fmu
            step/Step.fmu
            ramp/Ramp.fmu
            sine/Sine.fmu
        math/
            gain/Gain.fmu
            weighted_sum/WeightedSum.fmu
            product/Product.fmu
        continuous/
            integrator/Integrator.fmu
            first_order/FirstOrder.fmu
        discrete/unit_delay/UnitDelay.fmu
        controllers/pid/PID.fmu
        nonlinear/saturation/Saturation.fmu
        logic/switch/Switch.fmu
    # 每个模块目录还包含其 block.json

tools/standard_blocks/
    # 后续标准 FMU 源代码、构建脚本和构建说明
```

FMU builder 不属于 runtime application 或 infrastructure 路径。标准资源必须通过
Python package resource mechanism 定位，前端不得自行按 `site-packages` 路径寻找
FMU。后端 catalog/application layer 负责 resource resolution。

从 Phase 8.3 起必须增加从已安装 wheel 读取标准 `block.json` 和 FMU 的 regression
test，并验证最终 wheel 实际包含 `catalog.json`、每个 `block.json` 与 FMU。如
Hatchling 需要显式 include/package-data 配置，应只在真实资产加入时修改
`pyproject.toml`；8.0 不为不存在的资产预设配置或空文件。

## 4. 稳定分类与模块标识

分类 machine identifier 与中文显示名冻结为：

| category_id | 显示名称 |
|---|---|
| `sources` | 信号源 |
| `math` | 数学运算 |
| `continuous` | 连续系统 |
| `discrete` | 离散系统 |
| `controllers` | 控制器 |
| `nonlinear` | 非线性 |
| `logic` | 逻辑与切换 |

“显示与输出”不进入 executable standard block catalog：Scope、数值显示和曲线
显示属于 PySide6 frontend。

v1 stable `block_id` 冻结为：

| block_id | 显示名称 | category_id |
|---|---|---|
| `farcel.sources.constant` | 常量 | `sources` |
| `farcel.sources.step` | 阶跃 | `sources` |
| `farcel.sources.ramp` | 斜坡 | `sources` |
| `farcel.sources.sine` | 正弦信号 | `sources` |
| `farcel.math.gain` | 增益 | `math` |
| `farcel.math.weighted_sum` | 加权求和 | `math` |
| `farcel.math.product` | 乘法 | `math` |
| `farcel.continuous.integrator` | 积分器 | `continuous` |
| `farcel.continuous.first_order` | 一阶惯性环节 | `continuous` |
| `farcel.discrete.unit_delay` | 单位延迟 | `discrete` |
| `farcel.controllers.pid` | PID 控制器 | `controllers` |
| `farcel.nonlinear.saturation` | 饱和环节 | `nonlinear` |
| `farcel.logic.switch` | 开关 | `logic` |

`block_id` 一旦公开即为稳定 machine identifier，不能随意变更。中文 display
metadata 可以演化，但不得借此更改 block ID。

## 5. 未来 Farcel-owned descriptors

Phase 8.1 将定义、但 8.0 **不创建**以下 Farcel-owned contracts：

- `BlockCategoryDescriptor`
- `BlockDescriptor`
- `BlockParameterDescriptor`
- `BlockPortDescriptor`
- `BlockValueType`
- `BlockPortDirection`

可复用既有 `InterfaceType`。descriptor 不得包含 FMPy class、PySide6 class、
`QPoint`/`QRect`、GUI coordinate、native FMU handle、Worker RPC object 或 socket
object。

### 5.1 on-disk `block.json` schema 与 contract 映射

每个 `block.json` 的 v1 根对象使用 `schema_version: "1.0"`，至少具有下列字段：

```json
{
  "schema_version": "1.0",
  "block_id": "farcel.math.gain",
  "block_version": "1.0.0",
  "display_name": "增益",
  "description": "将输入乘以指定增益。",
  "category_id": "math",
  "category_display_name": "数学运算",
  "fmu_asset": "math/gain/Gain.fmu",
  "fmu_sha256": "<FMU 的 SHA-256>",
  "execution_interface": "FMI_2_CO_SIMULATION",
  "parameters": [],
  "input_ports": [],
  "output_ports": []
}
```

根字段映射为未来 `BlockDescriptor`：`schema_version`、`block_id`、
`block_version`（semantic-version 风格，例如 `"1.0.0"`）、`display_name`、
`description`、`category_id`、`category_display_name`、`fmu_asset`、`fmu_sha256`、
`execution_interface`、`parameters`、`input_ports`、`output_ports`。

`parameters` 的每项映射为 `BlockParameterDescriptor`，至少有
`variable_name`、`display_name`、`description`、`value_type`、`default_value`、
`unit`、`minimum`、`maximum`、`display_on_block`、`display_order`。`input_ports`
和 `output_ports` 的每项映射为 `BlockPortDescriptor`，至少有 `variable_name`、
`display_name`、`description`、`direction`、`value_type`、`unit`、`order`。

`BlockCategoryDescriptor` 至少有 `category_id`、`display_name`、`description`、
`order`。`BlockValueType` v1 至少定义 `Real`；`BlockPortDirection` 定义 `INPUT`
和 `OUTPUT`。`variable_name` 是 FMI machine identifier；`display_name` 和
`description` 是用户可见的简体中文。

### 5.2 模块方框中的参数显示

后端 descriptor 只提供 `display_on_block` 和 `display_order`。例如 PID 的 `kp`、
`ki`、`kd` 可标记为 `display_on_block: true`。前端负责是否显示、方框宽高、文字
位置、字体、行间距、图标、端口位置和属性面板。descriptor 禁止保存 GUI x/y
coordinates，前端也不得以 `if block_id == PID` 或 `if block_id == Gain` 硬编码
参数显示规则。

## 6. v1 模块数学和端口语义

所有端口与参数均为 scalar `Real`。`t` 是逻辑仿真时间；`h` 是当前 communication
step。以下 `variable_name`、端口和参数是 v1 的稳定 FMI-facing 定义，名称与说明
在 descriptor 中提供简体中文显示 metadata。

| 模块 | 输入 / 参数 | 输出与冻结数学语义 |
|---|---|---|
| 常量 | 参数 `value` | `y = value`。 |
| 阶跃 | 参数 `initial_value`、`final_value`、`step_time` | `t < step_time` 时 `y = initial_value`；`t >= step_time` 时 `y = final_value`。 |
| 斜坡 | 参数 `offset`、`slope`、`start_time` | `t < start_time` 时 `y = offset`；否则 `y = offset + slope * (t - start_time)`。 |
| 正弦信号 | 参数 `amplitude`、`frequency_hz`、`phase_rad`、`offset` | `y = offset + amplitude * sin(2*pi*frequency_hz*t + phase_rad)`。 |
| 增益 | 输入 `u`；参数 `gain` | `y = gain * u`。 |
| 加权求和 | 输入 `u1`、`u2`；参数 `k1`、`k2`、`bias` | 固定两输入，`y = k1*u1 + k2*u2 + bias`；不实现任意数量输入。用于 setpoint - feedback 时设 `k1 = 1`、`k2 = -1`。 |
| 乘法 | 输入 `u1`、`u2` | `y = u1 * u2`。 |
| 积分器 | 输入 `u`；参数 `initial_value` | 状态/输出为 `y`，连续概念 `dy/dt = u`。FMI2 CS 的 8.4 实现采用 communication interval 内 ZOH input 的积分语义，并建立数值回归。 |
| 一阶惯性环节 | 输入 `u`；参数 `gain`、`time_constant`、`initial_value` | `time_constant * dy/dt + y = gain * u`，要求 `time_constant > 0`。FMI2 CS 首版在 ZOH input 下采用稳定、明确的离散更新，而非依赖外部 solver。 |
| 单位延迟 | 输入 `u`；参数 `initial_value` | 初始化 `y = initial_value`；此后每个 communication checkpoint 输出上一采样输入。 |
| PID 控制器 | 输入 `e`；参数 `kp`、`ki`、`kd` | 输出 `u`；离散 PID 定义见下文。 |
| 饱和环节 | 输入 `u`；参数 `lower_limit`、`upper_limit` | 要求 `lower_limit <= upper_limit`，`y = clamp(u, lower_limit, upper_limit)`。 |
| 开关 | 输入 `u_true`、`u_false`、`control`；参数 `threshold` | `control >= threshold` 时 `y = u_true`，否则 `y = u_false`。v1 不引入 Boolean control。 |

PID 初始化积分状态 `I_0 = 0`，previous error 未定义并视为首次采样。第一个
sample 的 derivative 为 `D_0 = 0`，并记录 `e_0`。每个其后的 sample，以当前
误差 `e_k` 和 communication step `h > 0` 计算：

```text
I_k = I_(k-1) + e_k * h
D_k = (e_k - e_(k-1)) / h
u_k = kp * e_k + ki * I_k + kd * D_k
```

8.4 必须将该离散定义、`h` 的使用与边界 sample 建立数值回归。v1 不包含
anti-windup、derivative filtering、bumpless transfer 或 runtime retuning。

## 7. 参数更新边界

第一版参数修改只允许以下生命周期：

```text
STOPPED -> 修改 ModelNodeConfig.parameters -> validate -> Run
```

禁止运行中修改 parameter、live PID tuning 和 runtime structural parameter update。

## 8. Project 集成与资产不变性

`PROJECT_SCHEMA_VERSION` 保持 `"1.0"`；Phase 8 不引入 project schema 1.1。未来
将标准模块加入 `SimulationProject` 时，必须执行：

```text
package standard FMU -> materialize/copy -> project-relative path
  -> ModelAsset -> SHA-256 -> SimulationCase / ModelNode
```

canonical project path 冻结为：

```text
models/standard/<block_id>/<block_version>/<fmu_filename>
```

例如：`models/standard/farcel.controllers.pid/1.0.0/PID.fmu`。同一标准 FMU 被多
个 node 使用时复用同一 `ModelAsset`。相同 canonical path 加相同 SHA 允许复用；
相同 path 加不同 SHA 不得静默覆盖，后续必须产生明确错误。

项目保存后必须 self-contained。安装的 Farcel 标准库未来升级，也不得改变已有
Project 内已 materialize 的 FMU。重新打开项目时，后端可用 canonical standard
path 加 FMU SHA-256 识别 `block_id` / `block_version`，但不为保存 block ID 改动
project schema。

## 9. descriptor 与真实 FMU 的一致性

Phase 8.5 必须验证 `block.json` 与真实 FMU metadata：

- 每个 parameter `variable_name` 存在、类型一致，并具有合法 causality/variability；
- 每个 input port 存在且 causality 为 input；每个 output port 存在且 causality 为 output；
- port type 一致；
- default value 与设计一致；
- 每个 `display_on_block` parameter 对应真实 parameter；以及
- `execution_interface` 与 FMU capability 一致。

descriptor 不得成为与真实 FMU 脱节的 UI 假描述。

## 10. 分布式和数值边界

Phase 8 不改变 Worker protocol。既有 `WorkerAssetStager` 已能将普通 local FMU
path 以 SHA-256 经 `HAS_ASSET` / `PUT_ASSET` 放入 Worker cache；标准模块只要解析
为普通 FMU path，就自然兼容 LOCAL、LOCAL/WORKER mixed 与 two-Worker。Phase 8.7
的职责是验证这一兼容性，不是创造新的 distributed runtime。`run_project_case` 仍
为 local-only，Phase 8 不顺手扩展此边界。

标准模块进入 `SimulationGraph` 后继续服从 explicit Jacobi、previous-checkpoint
routing。Gain、WeightedSum、PID、Switch、FirstOrder 等连接均受 communication
checkpoint 语义约束；Phase 8 不实现 algebraic-loop solver、fixed-point iteration、
Newton iteration 或 strong coupling。

Phase 8.8 的闭环示例冻结为：

```text
目标值 -> Step -> Weighted Sum <- feedback
                     ↓
                    PID -> First Order -> system output
```

其中 Weighted Sum 使用 `k1 = 1`、`k2 = -1`。用户修改 `Step.final_value`、PID 的
`kp` / `ki` / `kd` 和 First Order 的 `gain` / `time_constant` 后重新运行，应获得
明显不同响应。该示例是现有 Jacobi 语义下的 PID 闭环，不得宣传为 zero-delay
strong coupling。Scope 只消费 canonical `GraphSimulationResult`，绘图仍属于前端。

## 11. 计划阶段与非目标

Phase 8 后续工作按以下顺序规划：

| 子阶段 | 范围 |
|---|---|
| 8.0 | architecture/design freeze |
| 8.1 | Farcel-owned descriptor contracts |
| 8.2 | catalog |
| 8.3 | source/math FMUs |
| 8.4 | dynamic/control FMUs |
| 8.5 | Block → ModelNode integration 与 descriptor/FMUs 一致性验证 |
| 8.6 | Project integration |
| 8.7 | distributed compatibility 验证 |
| 8.8 | closed-loop demo |
| 8.9 | release gate |

Phase 8 的明确非目标是：GUI canvas、GUI library tree implementation、Scope
drawing、display widget、icons、LAN distributed、HIL、real-time、ROM、SysML
import、Simulink/AMESim/ANSYS direct adapter、FMI3 Model Exchange、Scheduled
Execution、strong coupling、Newton/fixed-point、checkpoint/restart、runtime
parameter tuning、project schema 1.1，以及 cloud/cluster Worker。

## 12. 简体中文规则

所有用户可见的 Phase 8 字段和提示必须使用简体中文：`display_name`、
`description`、category display name、parameter display name/description、port
display name/description 与 help text。Python class/function、`block_id`、
`category_id`、JSON key、FMI `variable_name`、enum value、protocol field 和内部
machine identifier 可使用英文。例如用户看到“PID 控制器”“比例系数”，机器读取
`farcel.controllers.pid` 和 `kp`。
