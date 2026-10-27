# Phase 9.4B：前端参数编辑器契约

## 1. 适用范围与最终判定

本规范面向 PySide6 前端。前端只使用 Farcel 的公开 `ModelMetadata`、
`VariableMetadata`、`SimulationConfig` 和：

```python
metadata = backend.load_fmu(path)
backend.validate_config(metadata, config)
```

无需增加 ParameterService、额外 DTO 或第二套 FMI 校验器。前端可以依据 metadata
选择合适控件，但任何提交前都必须调用 `validate_config()`；其 `CONFIG_ERROR` 的
`details["issues"]` 是最终合法性结论。不要让 GUI 通过猜测 FMI 规则绕过后端验证。

## 2. VariableMetadata 到控件的映射

| metadata 字段 | 前端用途 | 约束 |
|---|---|---|
| `name` | 控件标识、映射到配置键 | 不按名称推断类别。 |
| `causality` | 判断 parameter、input、output 或只读项 | 是编辑权限的首要依据。 |
| `variability` | 补充说明 fixed/tunable/continuous/discrete 等 FMI 声明 | 不单独赋予运行中写权限。 |
| `data_type` | 选择数值、布尔、字符串或枚举控件 | 仍以 `validate_config()` 结果为准。 |
| `initial`、`start` | 显示 FMU 导出的初值/默认值 | 空值表示 FMU 未提供可显示默认值。 |
| `minimum`、`maximum` | 控件范围提示、预校验 | 后端最终校验范围。 |
| `unit`、`display_unit` | 标签和单位换算显示 | 不改变传入 FMU 的基础值。 |
| `description`、`declared_type` | 帮助文本与类型说明 | 仅展示，不能推断可写性。 |
| `shape`、`dimension_value_references` | 数组编辑器或只读提示 | 受 FMI 版本和类型限制。 |

## 3. 变量分类与编辑行为

| 分类 | metadata 条件 | GUI 行为 | 写入位置 |
|---|---|---|---|
| 初始化 parameter | `causality="parameter"` 且类型/shape 被当前验证器支持 | 参数面板；每次运行前编辑 | `SimulationConfig.parameters` |
| FMI 3 structural parameter | `fmi_version="3.0"` 且 `causality="structuralParameter"` | 仅显示当前受支持的标量整数/枚举场景，并提示其配置期限制 | `SimulationConfig.parameters` |
| 初始 input | `causality="input"` | 输入初值或输入连接控件 | `SimulationConfig.initial_inputs` |
| 计划 input | `causality="input"` | 通信点时序表；零阶保持 | `SimulationConfig.input_schedule` / `InputUpdate` |
| output | `causality="output"` | 只读显示，可选为结果列 | `selected_outputs`，不写入变量 |
| state/local/independent/未知 causality | 其余值 | 只读元数据；不提供普通覆写控件 | 无 |

所有 `parameter` 都是“初始化前候选项”，不是运行时实时可调承诺。具体 FMU 是否
接受某次设置最终由 FMI metadata、Farcel 校验和该 FMU 的 runtime 决定。

## 4. 当前类型与数组支持

- 标量 `Real`/`Float32`/`Float64`：数值控件；值必须有限。
- 整数、无符号整数与 `Enumeration`：整数/枚举控件；后端检查类型范围。
- `Boolean`：复选框。
- `String`：文本框。
- FMI 2 标量 parameter/input 使用相应 setter；FMI 3 支持当前 runtime 已实现的
  标量类型。
- 数组 parameter/input 仅在当前 shape 可解析、元素类型与范围均合法时支持；控件
  必须按照 `shape` 提供嵌套值，不能扁平化后猜测维度。
- FMI 3 structuralParameter 当前仅接受标量整数/无符号整数/枚举；数组 structural
  parameter 及其他 structural 类型不能显示为可编辑。
- 当前不支持的类型或 shape 应显示为只读和“当前版本不支持编辑”，而非将其塞入
  配置后期待 runtime 失败。

## 5. 初始化与运行中更新的边界

```python
from farcel.contracts import InputUpdate, SimulationConfig

config = SimulationConfig(
    start_time=0.0,
    stop_time=2.0,
    communication_step=0.01,
    parameters={"T": 0.5},       # 初始化前 parameter override
    initial_inputs={"u": 1.0},   # 初始化前 input 值
    input_schedule=(
        InputUpdate(time=1.0, values={"u": 2.0}),  # 从 t=1.0 开始的下一个 step 生效
    ),
    selected_outputs=("y",),
)
```

Co-Simulation runner 在每个 step 的开始检查 schedule。因此位于 `t=1.0` 的结果样本
是旧输入从前一通信点积分到达的值；`[1.0, 1.01]` 这一步使用新输入。输入是零阶
保持至下一次更新，不提供连续插值。时间必须严格递增、位于 `[start_time, stop_time)`，
并与 `communication_step` 的通信点对齐。schedule 不能写 parameter、state 或 output。

修改 `.mo`、方程、源码默认 parameter 值、FMI 版本或 FMU 类型需要重新编译并产生
新 FMU/Project asset。对同一已编译 FMU 修改 `parameters`、`initial_inputs`、
`input_schedule`、输出选择或采样设置均不需要重新编译，也不应改写 source、FMU 哈希
或 Project provenance。

## 6. FMI 2 与 FMI 3 差异

Farcel 当前可执行 FMI 2/3 Co-Simulation，并公开 FMI 2 Model Exchange；Modelica
编译仍只支持 FMI 2.0 Co-Simulation。FMI 3 structuralParameter 可能参与数组维度，
因此由 runtime 在 Configuration Mode 处理，并受上述严格类型限制。FMI 2 没有此
公开 structuralParameter 路径。无论 FMI 版本，运行中的 `input_schedule` 只面向
causality=input，不是 parameter 动态重设机制。

## 7. 诊断到控件反馈

调用 `validate_config()` 失败时，按 issue 的 `field` 定位控件并显示 Farcel 给出的
中文 `message`；不要解析 FMI/OMC 日志。

| field | 应定位的 GUI 区域 | 常见 code |
|---|---|---|
| `parameters` | 参数面板 | `UNKNOWN_PARAMETER`、`INVALID_PARAMETER_CAUSALITY`、`INVALID_PARAMETER_TYPE`、`PARAMETER_BELOW_MINIMUM`、`PARAMETER_ABOVE_MAXIMUM` |
| `initial_inputs` | 输入初值 | `UNKNOWN_INPUT`、`INVALID_INPUT_CAUSALITY`、`INVALID_INPUT_TYPE`、范围错误 |
| `input_schedule[n]` | 第 n 行输入时序表 | `INPUT_TIME_NOT_COMMUNICATION_POINT`、`INPUT_TIME_OUT_OF_RANGE`、`INPUT_TIMES_NOT_INCREASING`、输入类型/causality 错误 |
| `selected_outputs` | 输出选择 | `UNKNOWN_OUTPUT`、`UNSUPPORTED_OUTPUT_TYPE` |
| 时间/采样字段 | 仿真设置 | `INVALID_TIME_RANGE`、`INVALID_STEP_SIZE`、`OUTPUT_INTERVAL_NOT_COMMUNICATION_ALIGNED` |

前端预校验只是改善交互；收到 `CONFIG_ERROR` 后必须以返回的全部 issues 更新控件状态。
不要把原始 FMI/OMC 栈信息展示给普通用户。
