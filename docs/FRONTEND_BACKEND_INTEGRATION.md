# Frontend / Backend Integration Contract

本文档定义 Farcel GUI 应依赖的后端公共边界。它描述当前已经实现的同步 API，不是 GUI 实现方案，也不引入 PyQt、线程框架或新的运行时抽象。

## 1. Backend Capabilities

当前默认后端提供四个完整用例：

- inspect：读取 FMI 2.0 / 3.0 元数据，并区分“可解析”与“Farcel 当前可执行”。
- validate：在实例化前验证时间、执行能力、参数覆盖和所选输出。
- run：同步执行单个 FMI 2.0 或 FMI 3.0 Co-Simulation FMU，返回 canonical `SimulationResult`；FMI 3 Event Mode、Early Return，以及按默认或有效尺寸解析的数组按当前 capability 与 metadata 支持。
- export：将已经完成的 `SimulationResult` 导出为 CSV，不重新运行 FMU。

Model Exchange 与 Scheduled Execution 可以被 inspect，但当前不能 run。

## 2. Allowed Frontend Dependencies

GUI 只能从以下公共模块导入：

```python
from farcel import FarcelEngine, create_backend
from farcel.contracts import (
    EngineError,
    ErrorCode,
    ExportReport,
    InputUpdate,
    InterfaceType,
    ModelMetadata,
    ResultChunk,
    RunControl,
    RunProgress,
    SimulationConfig,
    SimulationResult,
    ValidationReport,
)
```

页面代码通常只需 `create_backend`、`SimulationConfig` 和 `EngineError`。`FarcelEngine` 主要用于类型标注；GUI 不应自行调用其构造函数组装 adapter。

## 3. Forbidden Frontend Dependencies

GUI 不得导入或依赖：

- `farcel.infrastructure` 或其任何子模块；
- `fmpy`、FMPy model description、FMU instance、native handle 或 value reference；
- `farcel.cli`、CLI 参数解析器、CLI 输出文本；
- application 的内部 validator 或 session registry；
- NumPy 专有对象作为公共数据模型。

CLI 是另一个消费者，不是 GUI API。GUI 不得启动 CLI 子进程或解析其 stdout。

## 4. Composition Entry Point

默认本地后端由唯一公开装配函数创建：

```python
from farcel import create_backend

backend = create_backend()
```

该函数封装当前 importer、session factory 和 CSV exporter 的选择。未来替换 FMPy backend 时，GUI 工作流无需了解具体实现。

## 5. Inspect Workflow

```python
metadata = backend.load_fmu(path)
```

输入为 `str | pathlib.Path`，成功时返回 `ModelMetadata`。GUI 可稳定使用：

- `model_name`、`description`、`fmi_version`；
- `interface_types` 与 `executable_interface`；
- `default_experiment`、`platforms`、`capabilities`；
- `variables` 中的 `name`、`data_type`、`causality`、`variability`、`shape`、`start`、`minimum`、`maximum` 和 `unit`；
- `diagnostics` 作为只读诊断文本。

`executable_interface is None` 表示 FMU 可成功解析，但不满足当前执行策略。`VariableMetadata.value_reference` 是 runtime 映射信息；GUI 不应读取或保存它。

失败时抛出 `EngineError`，常见 code 为 `IMPORT_ERROR`、`VALIDATION_ERROR`、`UNSUPPORTED_FMI` 或 `PLATFORM_BINARY_MISSING`。

## 6. Validation Workflow

```python
config = SimulationConfig(
    start_time=0.0,
    stop_time=2.0,
    communication_step=0.01,
    parameters={"mu": 2.0},
    selected_outputs=("x0",),
)
report = backend.validate_config(metadata, config)
```

`initial_inputs={"name": value}` is optional and is written during
initialization. Time-varying step inputs use the additive, optional
`input_schedule=(InputUpdate(time, values), ...)` field. Update times align with
communication points, are strictly increasing, and values are held until the
next update. Existing `SimulationConfig` calls that omit both fields retain the
same behavior.

成功返回 `ValidationReport`，且 `report.is_valid` 为 `True`。失败抛出 code 为 `CONFIG_ERROR` 的 `EngineError`；GUI 可读取 `error.details["issues"]`，其中每项包含稳定字段 `field`、`code`、`message`，用于定位控件和显示信息：

```python
try:
    backend.validate_config(metadata, config)
except EngineError as error:
    if error.code is ErrorCode.CONFIG_ERROR:
        for issue in error.details.get("issues", ()):
            show_field_error(issue["field"], issue["message"])
```

不要通过解析 `str(error)` 或 CLI 文本恢复字段错误。

对于 FMI 3 数组，参数、initial input 和每项 `InputUpdate.values` 接受严格匹配
有效 shape 的 nested Python sequence（list/tuple；字符串和 bytes 不视为 array
sequence）。无结构参数覆盖时，有效 shape 等于 `VariableMetadata.shape`；带 dimension
value reference 的数组在标量整型或枚举型 `structuralParameter` 覆盖后，按覆盖值解析。
值在公共边界以 nested tuple 表示；shape、元素类型和元素范围错误仍以稳定的
CONFIG_ERROR issue 返回。结构参数数组、Reconfiguration Mode、运行中结构参数改变
仍不在当前范围。

FMI 3 Binary 与 Clock 变量可以显示在 metadata 中，但当前不能作为运行时数据通道。向
Binary input 提交值会返回 `UNSUPPORTED_INPUT_TYPE`；选择 Binary 或 Clock output 会返回
`UNSUPPORTED_OUTPUT_TYPE`，二者都发生在 validation 阶段，不会调用 FMPy getter/setter。
只声明 Scheduled Execution 的 FMU 同样可 inspect，但 `run_fmu` 会在创建 native session 前
返回 `CONFIG_ERROR` / `UNSUPPORTED_INTERFACE`。

## 7. Sampling Semantics

`communication_step` is the Co-Simulation communication-point step. The optional
`output_interval` independently controls when Farcel records a `SimulationResult`
sample. When omitted (`None`), it resolves to `communication_step` for backward
compatibility. An explicit interval must be a positive, finite integer multiple
of `communication_step`; this Phase 2.0A rule guarantees every sample lies on an
actual communication point and Farcel does not interpolate values.

`completed_steps` is the number of successfully completed FMU communication
steps, while `sample_count` is the number of stored result samples. They no
longer have a fixed relationship. Farcel records the initial state and, after a
successful run, appends the final state if that communication point was not
already sampled. Empty `selected_outputs` still produces this sampled timeline,
but keeps `outputs` empty. Input schedules remain applied at communication
points, independently of result sampling.

For FMI 3 Co-Simulation, `supports_event_mode` and `supports_early_return` in
the existing capability metadata determine whether Farcel enables the respective
native lifecycle flag. Event Mode is handled inside the infrastructure adapter.
An Early Return may make `RunProgress.current_time` temporarily fall between
configured communication points, but Farcel resumes the same configured target.
It does not add a normal output sample, change `completed_steps`, skip an
`input_schedule` update, or independently emit a `ResultChunk` until that target
is reached. Intermediate Update remains unsupported as a public callback API.

## 8. Controlled Run and Progress

The high-level call remains synchronous and blocking, with backward-compatible
keyword-only controls:

```python
result = backend.run_fmu(path, config, control=control, on_progress=callback)
```

`RunControl.request_stop()` is thread-safe and may be called from another
caller-owned thread. It is cooperative: Farcel checks it before every
communication step, so the maximum response delay is the currently executing
native `doStep()`. Farcel cannot hard-kill or otherwise interrupt that call.
Farcel itself creates no threads, event loop, or GUI integration.

`on_progress` receives a lightweight `RunProgress` after initialization, after
each successful communication step, and once in a terminal state. Its fraction
is the clamped real-time ratio `(current_time - start_time) / (stop_time -
start_time)`. The callback executes on the same thread that invoked `run_fmu`;
GUI callers must marshal it to the UI thread themselves. A callback exception is
converted to `INTERNAL_ERROR` and does not skip terminate/close cleanup.

A stop requested before loading raises `CANCELLED` without creating an FMU
session. After initialization, a user stop returns `SimulationResult` with
`completion_state=STOPPED` rather than an engine error. The final stopped state
is appended when it was not an output sample, and a stopped result remains
exportable through `export_result()`.

### Result chunk streaming

The same keyword-only API also accepts `on_result_chunk` and
`result_chunk_size` (default `256`):

```python
result = backend.run_fmu(
    path,
    config,
    control=control,
    on_progress=on_progress,
    on_result_chunk=on_result_chunk,
    result_chunk_size=256,
)
```

`result_chunk_size` must be an integer greater than zero (not `bool`). Invalid
values fail before FMU loading with `CONFIG_ERROR` and an issue whose stable
`field` is `result_chunk_size` and `code` is `INVALID_RESULT_CHUNK_SIZE`.

`on_result_chunk` receives `ResultChunk(run_id, sequence, time, columns,
final_chunk)`. `time` and each selected output column contain the same,
contiguous canonical result samples that will appear in `SimulationResult`;
they are not communication-step events and do not trigger extra output reads.
The initial sample is included. With no selected outputs, `columns == {}` but
the sampled time axis still streams. Every run uses a fresh UUID `run_id` and
zero-based contiguous sequence values.

Farcel holds a full `SimulationResult` as before; streaming is not a
bounded-memory execution mode. A completed or cooperatively stopped run emits
exactly one non-empty `final_chunk=True` only after successful FMU termination,
and before terminal progress. A runtime or termination engine error emits no
extra final chunk.
The chunk callback uses the `run_fmu` thread. Its exception is converted to
`INTERNAL_ERROR` with diagnostic key `chunk_callback_diagnostic` and does not
skip cleanup; GUI code must marshal callback data to the UI thread itself.

An FMI 3 array output remains one public column keyed by its declared variable
name. Each item in that column is a nested tuple with the declared shape;
concatenating chunks reproduces `SimulationResult.outputs[name]` exactly.

## 9. Run Workflow

```python
result = backend.run_fmu(path, config)
```

`run_fmu` 会重新加载 metadata、复用 application validation、创建 session、初始化、执行 step、仅在结果采样时读取选择的输出并清理资源。成功返回 `SimulationResult`；GUI 不需要也不应直接管理 session 生命周期。

当前公开高层运行 API 是同步、阻塞调用。GUI 必须在自己的调度边界之外调用它（例如 GUI 框架认可的后台任务），绝不能在 UI event-loop 线程直接运行长仿真。只有 `RunControl.request_stop()` 可安全从另一个线程调用；FarcelEngine 整体并不声明 thread-safe，也不提供 asyncio 或 timeout hard-kill。`on_progress` 与 `on_result_chunk` 均在 run 调用线程执行。

## 10. Consuming SimulationResult

`SimulationResult` 是纯 Farcel / Python 数据：

- `timestamps`：记录的、严格递增的实际 communication points；首项为 `start_time`，完成或停止时末项为 `final_time`；
- `outputs`：变量名到同长度 tuple 的映射，只包含 `selected_outputs`；
- `start_time`、`stop_time`、`step_size`、`final_time`；
- `completed_steps`、`sample_count`、`completion_state` 和 `successful`。

每个输出序列与 `timestamps` 等长，首项是初始化后的 start-time 样本。无所选输出时，`outputs` 为空，但时间轴和执行摘要仍按 `output_interval` 存在。GUI 可按索引将 `timestamps[i]` 与每个 `outputs[name][i]` 配对；不应重新推导时间轴。

FMI 3 array output 仍只占一个 `outputs[name]` key；其每个样本是 immutable-friendly nested tuple，而不是拆分成 `name[0]` 等 output key。带结构参数覆盖的运行中，GUI 应按同一次运行所提交配置导出的有效 shape 展示或绘图，而不是把导入 metadata 的默认 `shape` 当作运行时 shape。

## 11. Export Workflow

```python
report = backend.export_result(result, destination)
```

输入是完成或停止后的 `SimulationResult` 与 `str | Path` 目标；返回 `ExportReport(destination, row_count)`。当前 exporter 写 UTF-8 CSV、创建父目录并覆盖同名文件。标量 output 保持单列；array output 展开为稳定的零基 indexed columns（例如 `y[0]`、`A[0,0]`），且 `row_count` 始终等于 sample_count。导出不会重新执行 FMU，`STOPPED` partial result 也可导出。未配置 exporter 或写文件失败时抛 `EXPORT_ERROR`。

## 12. Error Contract

所有面向 GUI 的后端失败均使用：

```python
EngineError(code: ErrorCode, message: str, details: Mapping[str, Any])
```

`code` 供程序分支，`message` 供用户阅读，`details` 供诊断。除 validation 的 `details["issues"]` schema 外，其他 details 应视为可扩展诊断信息，不要用具体底层异常文本驱动 UI 状态。

当前相关错误类别包括：

- 导入/能力：`IMPORT_ERROR`、`VALIDATION_ERROR`、`UNSUPPORTED_FMI`、`UNSUPPORTED_INTERFACE`、`PLATFORM_BINARY_MISSING`；
- 配置：`CONFIG_ERROR`；
- runtime：`INSTANTIATION_ERROR`、`INITIALIZATION_ERROR`、`PARAMETER_SET_ERROR`、`INPUT_SET_ERROR`、`STEP_ERROR`、`OUTPUT_READ_ERROR`、`TERMINATION_ERROR`、`CLEANUP_ERROR`、`FMI_RUNTIME_ERROR`；
- 导出/通用：`EXPORT_ERROR`、`INTERNAL_ERROR`、`NOT_IMPLEMENTED`。

`CANCELLED` 表示 RunControl 在运行开始前已经请求停止；初始化后请求的用户 Stop 返回 `STOPPED` result。`TIMEOUT` 仍未实现，Farcel 不会强制终止 native FMU。

## 13. FMI Version Transparency

GUI 对 FMI 2.0 与 FMI 3.0 Co-Simulation 使用同一组调用和 DTO：`load_fmu`、`validate_config`、`run_fmu`、`export_result`。版本差异通过 `ModelMetadata.fmi_version`、interfaces 和 capabilities 展示；不要根据版本选择 FMPy class 或调用不同 getter。

Farcel 处理 capability-enabled FMI 3 Event Mode 与 Early Return，并支持默认或由标量整型/枚举型结构参数覆盖解析的 FMI 3 arrays；对存在这类覆盖的运行使用一次 Configuration Mode。它不提供 Intermediate Update 数据回调、结构参数数组、Reconfiguration Mode、运行中结构参数改变、Binary 或 Clock。未声明相应 capability 的 FMU 不会被强制启用高级模式；其他不支持的 FMI 3 条件仍返回稳定的 `EngineError`，不会暴露 FMPy status。

## 14. End-to-End Example

仓库中的 `examples/backend_api_example.py` 演示完整公共工作流：

```powershell
.\.venv\Scripts\python.exe .\examples\backend_api_example.py
.\.venv\Scripts\python.exe .\examples\backend_api_example.py .\examples\fmus\VanDerPol-fmi3.fmu
```

示例仅导入 `farcel`、`farcel.contracts` 和标准库，并依次完成 inspect、config、validate、run、result consumption 与 CSV export。

## 15. Contract Freeze Recommendation

进入 GUI 集成时建议冻结以下 v1 消费面：

- `create_backend()`；
- 高层方法 `load_fmu`、`validate_config`、`run_fmu`、`export_result`；
- 本文列出的 `ModelMetadata`、`SimulationConfig`、`ValidationReport`、`SimulationResult`、`ResultChunk`、`RunControl`、`RunProgress`、`ExportReport` 字段；
- `EngineError.code/message/details`，以及 CONFIG_ERROR 的 issue `field/code/message` schema；
- FMI 2/3 对同一高层工作流透明的原则。

以下部分应继续演进而不作为 GUI v1 依赖：低层 session/step 方法、session handle、FMI 3 advanced flags、`RunSummary`、未实现配置项的运行语义、基础设施类、diagnostic details 的自由文本和 `value_reference`。新增字段和错误码应保持向后兼容；删除或改义冻结字段时再提升 contract schema / major version。
