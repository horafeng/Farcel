# FMU Compatibility Matrix

This matrix records results reproduced on Windows win64 with Python 3.13 and
the pinned FMPy 0.3.31 dependency. `inspect` means the public
`backend.load_fmu()` path, not merely opening the ZIP container.

| FMU | Inspect | Validate | Run | Input | Output | CSV | Note |
|---|---:|---:|---:|---:|---:|---:|---|
| `Stair.fmu` | Yes | Yes | Yes | N/A | Yes | Yes | FMI 2 CS; `counter` changes from 1 to 2 over 0..1 s. |
| `VanDerPol.fmu` | Yes | Yes | Yes | N/A | Yes | Yes | FMI 2 CS; `mu` override and selected `x0` regression pass. |
| `VanDerPol-fmi3.fmu` | Yes | Yes | Yes | N/A | Yes | Yes | FMI 3 Co-Simulation；`mu` 覆盖和选定 `x0` 回归通过。 |
| `BouncingBall-fmi3.fmu` | Yes | Yes | Yes | N/A | Yes | Yes | Official Reference-FMUs v0.0.41 FMI 3 CS; Event Mode and Early Return, with `h`/`v` bounce regression. |
| `StateSpace-fmi3.fmu` | Yes | Yes | Yes | 初始值与计划数组 | 数组 `y` | 带索引数组列 | 官方 Reference-FMUs v0.0.41 原始 `3.0/StateSpace.fmu`；默认 `(3, 3)`/`(3,)` 尺寸与普通数组参数回归。原生 `setUInt64()` 缺陷使结构参数正向运行不可用，原始二进制保持未修改。 |
| `StateSpace-fmi3-patched.fmu` | Yes | Yes | Yes | 初始值、计划数组与动态有效尺寸 | 动态数组 `y` | 带索引数组列 | 仅供回归的本地构建 fixture，不是官方原始二进制；由 v0.0.41 源码应用 `examples/fmus/patches/StateSpace-v0.0.41-setUInt64.patch` 后构建，覆盖 `m=2,n=4,r=1`。 |
| `Feedthrough-fmi3.fmu` | Yes | Yes | Yes | 15 个受支持 scalar input 变量与计划更新，覆盖 13 种 runtime 数据类型 | 对应 scalar output；Binary 稳定拒绝 | Yes | 官方 v0.0.41 `3.0/Feedthrough.fmu`；Float32/64、全部 Int/UInt、Boolean、String、Enumeration 真实 round-trip，Binary 可 inspect 但不进入 runtime。 |
| `Resource-fmi3.fmu` | Yes | Yes | Yes | N/A | `Int32 y` | Yes | 官方 v0.0.41 `3.0/Resource.fmu`；已验证解压目录中的 `resources/y.txt`，0..1 s 返回 `(97, 97)` 并完成 native/临时目录清理。 |
| `Clocks-fmi3.fmu` | Yes | 是，稳定拒绝 | No | N/A | Clock 仅 metadata | No | 官方 v0.0.41 `3.0/Clocks.fmu`；仅声明 Scheduled Execution，`UNSUPPORTED_INTERFACE` 在 session 创建前返回。 |
| `bouncingBall.fmu` | No | No | No | N/A | N/A | No | Container metadata says FMI 1.0 Model Exchange. Farcel intentionally supports only FMI 2/3 metadata and only CS execution. `UNSUPPORTED_FMI` includes `fmi_version=1.0` and `parseable=true`. |
| `manipulator.fmu` | Yes | Yes | No | Setters work | Initial sample only | No | FMI 2 CS. First `doStep` returns error because the native FMU reports `Singular matrix not invertible (getrf).`; reproduced with zero/non-zero inputs, explicit defaults, and step sizes 0.1 through 0.0001. Farcel reports the native diagnostic and releases resources. |
| `LateralMotionControl.fmu` | Yes, with warnings | Yes | Yes | Initial + scheduled | Yes | Yes | FMI 2 CS. XML references undeclared unit `m/s`; this recoverable unit-definition problem is retained in metadata diagnostics. A generic boolean trigger schedule produces meaningful task execution. |

## Metadata inventory

| FMU | Inputs | Parameters | Outputs | Scalar types |
|---|---:|---:|---:|---|
| `Stair.fmu` | 0 | 0 | 1 | Integer |
| `VanDerPol.fmu` | 0 | 1 | 2 | Real |
| `VanDerPol-fmi3.fmu` | 0 | 1 | 2 | Float64 |
| `BouncingBall-fmi3.fmu` | 0 | 2 | 3 | Float64 |
| `StateSpace-fmi3.fmu` | 1 array | 5 arrays + 3 structural parameters | 1 array | Float64, UInt64 |
| `Feedthrough-fmi3.fmu` | 16 | 2 | 16 | Float32, Float64, Int8/UInt8, Int16/UInt16, Int32/UInt32, Int64/UInt64, Boolean, String, Binary, Enumeration |
| `Resource-fmi3.fmu` | 0 | 0 | 1 | Float64, Int32 |
| `Clocks-fmi3.fmu` | 4 | 0 | 7 | Float64, Int32, Clock |
| `manipulator.fmu` | 2 | 10 | 2 | Real |
| `LateralMotionControl.fmu` | 19 | 34 | 40 | Real, Integer, Boolean |
| `bouncingBall.fmu` | FMI 1 metadata has no declared input causality | 2 parameter variables (`g`, `e`) | No declared output causality | Real |

## Root causes

### bouncingBall

This fixture is FMI 1.0 Model Exchange only. It has no Co-Simulation interface,
so running it would require both FMI 1 support and a Model Exchange solver,
which are outside the current milestone. No implementation change attempts to
reinterpret it as an FMI 2/3 Co-Simulation FMU.

### manipulator

Instantiation, setup, initialization, initial input writes, and the initial
output read succeed. The first native `fmi2DoStep` fails and logs:

```text
Singular matrix not invertible (getrf).
```

The failure is unchanged by setting `ref1`/`ref2`, using explicit parameter
start values, or reducing the step size. It is therefore not caused by Farcel
omitting initial or time-varying inputs. The integration test asserts the exact
FMU diagnostic and cleanup instead of treating a generic `STEP_ERROR` as an
expected success condition.

### LateralMotionControl

Strict schema validation reports two undeclared `m/s` unit references. Farcel
allows only this recoverable validation category to continue and preserves both
messages in `ModelMetadata.diagnostics`; all other schema validation failures
remain errors.

The FMU can advance without external inputs, but its task blocks remain inactive
when all trigger inputs stay false. Meaningful behavior therefore needs input
updates at communication points. The regression sets `velocity=20` initially,
pulses the generic sensor activation/finished inputs, and observes `sens_out_4`
change from 10 to 20 while time and step outputs advance.

## Input schedule semantics

`SimulationConfig.input_schedule` is an optional tuple of `InputUpdate` values.
Each update time must align with a communication point, updates must be strictly
increasing, and values are held until changed. Farcel applies the update for the
current communication point immediately before `doStep`. With an empty schedule,
the original execution path is unchanged.

## Communication and sampling semantics

`SimulationConfig.communication_step` controls every Co-Simulation `doStep`
communication point. `output_interval` controls only when Farcel records an
already-reached point in `SimulationResult`; it never changes input scheduling
or the FMU step size. If omitted, `output_interval` defaults to the communication
step for Phase 1 compatibility. An explicit output interval must be a positive,
finite integer multiple of the communication step, so no interpolation is
required. Farcel records the initial state, samples each matching communication
point, and records the final state on successful completion when it was not
otherwise sampled. Consequently, `completed_steps` and `sample_count` are
independent metrics from Phase 2.0A onward.

## Controlled stop and partial results

The high-level run remains synchronous and blocking. `RunControl.request_stop()`
is thread-safe for a caller-owned control thread, while `run_fmu()` continues to
belong to one worker thread. Stop is cooperative and checked before each
communication step. Therefore it cannot interrupt a native FMU inside `doStep()`
and has no hard timeout/kill behavior.

A pre-start stop returns `CANCELLED` without creating a session. Once initialized,
stop returns a `STOPPED` partial result: it retains completed communication steps,
appends the actual final point if it was not sampled by `output_interval`, and can
be exported to CSV. Progress callbacks execute on the run thread; they contain no
result payload.

## Result chunk streaming

`run_fmu(..., on_result_chunk=callback, result_chunk_size=256)` streams batches
of the same samples stored in its final `SimulationResult`; a batch is never a
batch of FMI `doStep` calls. The initial sample and a stopped run's appended
final state are included. Streaming does not change compatibility, communication
steps, or output getter count, and Farcel still retains the complete result.

Each run emits a fresh UUID `run_id`, contiguous zero-based chunk sequences, and
an empty `columns` mapping for no-output runs. A normally completed or
cooperatively stopped run has exactly one non-empty terminal chunk marked
`final_chunk=True`; an FMU runtime error does not generate an extra terminal
chunk. Chunk callbacks run synchronously on the caller's run thread and callback
failures become stable `INTERNAL_ERROR` values after normal cleanup.

## FMI 3 Event Mode and Early Return

For a FMI 3 Co-Simulation FMU that declares `hasEventMode` and/or early-return
capability, Farcel passes the matching instantiate flags. It performs the
required initialization Event Mode discrete-state loop, and repeats the same
loop after a runtime event before returning to Step Mode. The loop has a 1000
iteration guard and reports stable `STEP_ERROR` diagnostics if it cannot settle.

Early Return keeps the configured communication grid intact: a returned
`lastSuccessfulTime` becomes the actual current time, and Farcel continues to
the original target before counting a completed interval or adding an ordinary
sample. This behavior is verified with the official `BouncingBall-fmi3.fmu`
fixture from Modelica Association Reference-FMUs v0.0.41 (BSD-2-Clause; see
`examples/fmus/Reference-FMUs-LICENSE.txt`).

FMI 3 Co-Simulation 的数组支持普通参数、initial/scheduled input、selected output、
canonical result、chunk 和带索引的 CSV 列。对于标量整型或枚举型结构参数覆盖，Farcel
在 Configuration Mode 中设置值，并以覆盖值解析动态有效 shape；导入 metadata 保留默认
shape。结构参数数组、Reconfiguration Mode、运行中结构参数改变、FMI 3 Binary/Clock、
Intermediate Update 公共回调、Scheduled Execution、Model Exchange 执行和 FMI 1 仍不支持。

## Phase 2.3 Extended Reference FMU Compatibility Matrix

Feedthrough、Resource 与 Clocks 均直接来自 Modelica Association Reference-FMUs v0.0.41
官方 `Reference-FMUs.zip`，许可证为 BSD-2-Clause，文件 SHA-256 记录在
`examples/fmus/README.md`。它们分别覆盖三类不同边界：已支持 scalar 类型的真实正向
运行、FMU resource 访问与关闭清理、以及可解析但不执行的 Scheduled Execution/Clock。

Feedthrough 的 Binary input 在 validation 阶段返回 `UNSUPPORTED_INPUT_TYPE`，Binary output
返回 `UNSUPPORTED_OUTPUT_TYPE`；这属于已验证的预期 unsupported 行为，不尝试调用 native
Binary setter/getter。Clocks 的 Clock metadata 可见，但其无 Co-Simulation interface，故
`UNSUPPORTED_INTERFACE` 必须在 native session/extraction 创建前返回。
