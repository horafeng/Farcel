# Phase 9.1B：Modelica 编译合同与请求验证

## 1. 目标与范围

本阶段为后续的 Modelica 编译 application 编排和 compiler adapter 固定最小的
Farcel-owned 请求、结果、诊断和验证边界。它不执行 `.mo` → `.fmu` 编译，不调用
`buildModelFMU`，也不向 `FarcelEngine` 增加 `compile_modelica()`。

依赖方向保持为：

```text
GUI / CLI → application → contracts ← infrastructure
```

因此 GUI/CLI 只能消费 `farcel.contracts` 的 DTO；真实 OpenModelica 调用将在获准的
后续阶段通过 `ModelicaCompiler` Port 的 infrastructure adapter 实现。

## 2. 编译请求合同

`ModelicaCompileRequest` 是不可变的 `@dataclass(frozen=True, slots=True)`：

| 字段 | 类型 | 默认值 | 语义 |
|---|---|---|---|
| `source_path` | `str | Path` | 无 | 单个本地 Modelica source 文件。 |
| `model_name` | `str` | 无 | 要编译的单个模型类。 |
| `output_directory` | `str | Path` | 无 | 已存在的本地 FMU 输出目录。 |
| `compiler_path` | `str | Path | None` | `None` | 可选的显式 compiler 路径；省略时由后续环境检测发现。 |
| `fmi_version` | `str` | `"2.0"` | 第一版固定 FMI 2.0。 |
| `fmu_type` | `str` | `"cs"` | 第一版固定 Co-Simulation。 |
| `timeout_seconds` | `float` | `120.0` | 本机同步编译的最大时长。 |

DTO 可以表达待验证的无效输入；规则统一由 `contracts.modelica_validation` 的纯共享
validator 执行，application 保留公开验证入口和 `CONFIG_ERROR` 映射，不在
`__post_init__` 再维护一份冲突规则。第一版只接受一个 `.mo`、一个指定模型、FMI 2.0
和 `cs`。它不接受 FMI 1.0/3.0、Model Exchange、批量编译、package 依赖解析、自定义
OMC 参数、原始 shell 参数或脚本片段。

`model_name` 仅支持未加引号的标识符和点分限定名，例如
`ExampleSystems.MotorController`。每个段必须匹配
`[A-Za-z_][A-Za-z0-9_]*`；带引号的 Modelica 标识符及其他复杂语法留待将来明确设计。

## 3. 结果与诊断合同

`ModelicaDiagnostic(severity, code, message)` 使用
`ModelicaDiagnosticSeverity.INFO/WARNING/ERROR` 表达稳定的 Farcel 诊断。`message` 是
简体中文用户提示；前端不得通过解析 OMC stdout/stderr 判断状态。

`ModelicaCompileResult` 只表示成功生成且应由 future adapter 核验的产物，字段为：

- `fmu_path`：本次任务的运行时 FMU 位置，不是 Project schema 持久化路径；
- `fmi_version`、`fmu_type`、`compiler_version`；
- `sha256`：完成写入后 FMU 内容的 SHA-256 摘要；
- `diagnostics`：不可变的 `tuple[ModelicaDiagnostic, ...]`。

失败仍使用既有 `EngineError`；本阶段的
`require_valid_modelica_compile_request()` 将无效 `ValidationReport` 映射为
`ErrorCode.CONFIG_ERROR`，不创造第二套竞争性错误机制。

## 4. ModelicaCompiler Port

contracts 中仅定义实现无关的最小 Protocol：

```python
class ModelicaCompiler(Protocol):
    def compile(self, request: ModelicaCompileRequest) -> ModelicaCompileResult: ...
```

Port 不引用 `omc.exe`、OMPython、subprocess、FMPy 或 Qt。本阶段没有生产实现，也没有
空壳 adapter；未来 OpenModelica adapter 必须先执行本节定义的请求验证，再负责真实
编译、产物结构检查、哈希和稳定诊断映射。

## 5. 纯请求验证

`validate_modelica_compile_request()` 由 `contracts.modelica_validation` 提供，并经
application 模块保持原有公开导入；它返回现有 `ValidationReport`，收集所有字段级
`ValidationIssue`，没有写入副作用：不创建/删除目录，不覆盖文件，也不启动外部进程。

| 字段 | 验证规则 |
|---|---|
| `source_path` | 非空、可处理、本地存在且为普通 `.mo` 文件。 |
| `model_name` | 非空，且符合上述单一/点分标识符边界。 |
| `output_directory` | 非空、可处理、已存在且为目录；不会由 validator 创建。 |
| `compiler_path` | 可省略；若提供，必须是非空 `str` 或 `Path`，但本阶段不检查可执行性。 |
| `fmi_version` | 只能为 `"2.0"`。 |
| `fmu_type` | 只能为 `"cs"`。 |
| `timeout_seconds` | 非布尔、有限、`0 < value <= 1800` 的数值。 |

验证不是 Modelica 语义检查：模型类是否真实存在、方程是否合法或依赖库能否加载，都由
后续真实 compiler adapter 判断。

## 6. 验证与后续边界

新增 contracts 和 application 测试覆盖默认值、不可变性、限定模型名、多个错误、路径规则、
选项规则、超时、compiler path、`ModelicaCompiler` 结构兼容及 `CONFIG_ERROR` 映射；测试
使用临时目录，不要求 OpenModelica，也不执行编译。Phase 9.1A 环境检测和 Phase 9.0
真实 FMU 回归继续作为兼容性验证。

Phase 9.1C 必须获得单独授权后，才可设计真实 compiler adapter 对 `ModelicaCompiler`
Port 的实现、编译诊断映射和产物核验。本阶段不自动开始该工作。

## 7. 验证记录（2026-10-20）

新增 Phase 9.1B contracts 测试 4 个、application validator 测试 10 个，均通过；它们
全部使用临时目录和 mock-free 纯路径检查，不依赖已安装的 OMC。Phase 9.1A 环境检测与
Phase 9.0 真实 OMC 回归也已开启执行：`D:\OpenModelica\bin\omc.exe`（1.26.3）能继续
编译 `FarcelFirstOrder.mo` 为 FMI 2.0 Co-Simulation FMU，并由 Farcel 完成运行和 CSV
导出。

完整后端回归按 contracts（10）、application（59）、unit（560）和 integration（136）
执行，总计 `765`：`762` 通过、`3` 个既有跳过、`0` 失败、`0` 错误。真实 OMC 环境检测
与 Phase 9.0 FMU 测试均包含在 integration 组中。
