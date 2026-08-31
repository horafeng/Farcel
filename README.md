# Farcel

Farcel 是一个本地优先的轻量级 FMU 仿真工具。当前后端已支持通过 FMPy 读取 FMI 2.0 / 3.0 FMU 元数据、仿真前配置验证，以及 FMI 2.0 Co-Simulation 和 FMI 3.0 Co-Simulation（包括 capability-gated Event Mode 与 Early Return）的 Session 执行、所选输出变量时序采样与 CSV 导出。

`communication_step` 是 FMU Co-Simulation 的通信点推进步长；`output_interval` 是保存到 `SimulationResult` 的结果采样间隔。未设置 `output_interval` 时它等于 `communication_step`，保持原有“每通信点一个样本”的行为。显式设置的采样间隔必须是通信步长的整数倍；Farcel 不插值，并会在正常完成时补记尚未采集的最终状态。

对于支持该 capability 的 FMI 3 Co-Simulation FMU，Farcel 在初始化后和运行时完成 Event Mode 的离散状态更新，再回到 Step Mode。合法 Early Return 只推进实际 `current_time`；Farcel 会继续请求同一个 configured communication target，因此不会改变 `communication_step` 网格、`completed_steps`、`output_interval`、输入调度或 `ResultChunk` 的既有语义。Intermediate Update 数据回调仍未作为公共功能提供。

FMI 3 Co-Simulation 的数组可用于参数覆盖、initial input、scheduled input 和 selected output。公共数组值使用与有效 shape 严格一致的 nested tuple（配置输入也接受同形状的 list/tuple sequence）；`SimulationResult` 与 `ResultChunk` 保留每个时间样本的数组值，不把数组元素变成公共 output key。CSV 将数组展开为稳定的零基索引列，例如 `y[0]`、`A[0,0]`。对于标量整型或枚举型 `structuralParameter`，Farcel 在初始化前按 FMI 3 Configuration Mode 写入覆盖值，并以结构参数的当前值解析带 dimension value reference 的数组有效 shape；静态 `VariableMetadata.shape` 保持导入时的默认值不变。数组结构参数、Reconfiguration Mode、运行中结构参数改变、Binary 和 Clock 仍不支持。

当前后端执行范围为 FMI 2 Co-Simulation 与 FMI 3 Co-Simulation。FMI 3 已通过官方 Reference FMU 验证 Event Mode、Early Return、默认与动态数组、标量 Structural Parameter、运行前 Configuration Mode、Float32/Float64、Int8/UInt8、Int16/UInt16、Int32/UInt32、Int64/UInt64、Boolean、String、Enumeration、initial/scheduled input、输出采样、Stop/Progress、ResultChunk、CSV 和 `resources/` 访问。当前 GitHub Actions CI 以 Windows runner 为主要覆盖环境。

仍不支持 FMI 1 runtime、Model Exchange runtime、Scheduled Execution runtime、Binary runtime、Clock runtime、Reconfiguration Mode、运行期间结构参数修改、Intermediate Update public callback、multi-FMU 和 SSP；Farcel 不声称完整支持所有 FMI 3。

官方 Reference FMU v0.0.41 已真实验证的 FMI 3 Co-Simulation scalar runtime 类型包括 Float32、Float64、Int8/UInt8、Int16/UInt16、Int32/UInt32、Int64/UInt64、Boolean、String 和 Enumeration。Binary 与 Clock 仍可在 metadata 中 inspect；Binary input 或 selected output 在 validation 阶段稳定拒绝，Clock 所在的 Scheduled Execution FMU 保持 inspect-only。Resource FMU 的 `resources/y.txt` 访问也已完成真实运行与 cleanup 回归。

设计说明见 [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md)。GUI 等调用者的公共 API、依赖规则和错误处理约定见 [docs/FRONTEND_BACKEND_INTEGRATION.md](docs/FRONTEND_BACKEND_INTEGRATION.md)。

## 本地开发

在仓库根目录创建项目虚拟环境并安装 editable package：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

若系统未安装 Windows `py` launcher，可将第一条命令替换为任一已安装 Python 3 的完整可执行文件路径加 `-m venv .venv`；后续命令仍统一直接使用 `.venv` 内的 Python。

日常命令直接使用项目虚拟环境的 Python，不要求执行 `Activate.ps1`：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m farcel.cli inspect .\examples\fmus\VanDerPol.fmu
.\.venv\Scripts\python.exe -m farcel.cli inspect .\examples\fmus\VanDerPol.fmu --json
.\.venv\Scripts\python.exe -m farcel.cli validate .\examples\fmus\VanDerPol.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter "mu=2.0" --output x0
.\.venv\Scripts\python.exe -m farcel.cli run .\examples\fmus\VanDerPol.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter "mu=2.0" --output x0
.\.venv\Scripts\python.exe -m farcel.cli run .\examples\fmus\VanDerPol.fmu --start-time 0 --stop-time 0.2 --step-size 0.01 --output-interval 0.05 --output x0
.\.venv\Scripts\python.exe -m farcel.cli export .\examples\fmus\VanDerPol.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter "mu=2.0" --output x0 --csv .\artifacts\VanDerPol.csv
.\.venv\Scripts\python.exe -m farcel.cli run .\examples\fmus\VanDerPol-fmi3.fmu --start-time 0 --stop-time 0.05 --step-size 0.01 --parameter "mu=2.0" --output x0
.\.venv\Scripts\python.exe .\examples\backend_api_example.py
```

如果本机策略允许，也可以自行激活 `.venv`，但 Farcel 的安装和验证不依赖虚拟环境激活。

CSV 导出按指定路径写入 UTF-8 文件，自动创建父目录并覆盖同名文件；不会自动补充 `.csv` 扩展名。标量保持单列格式，数组按零基索引展开为多个列。仓库内 `artifacts/` 用于本地验证输出，并已加入 Git ignore。

## Controlled Run API

`run_fmu()` remains a synchronous, blocking call. The caller owns its worker
thread; Farcel does not create threads or import a GUI framework. `RunControl`
is safe to call from another thread and requests a cooperative stop at the next
communication point. It cannot interrupt a native FMU already executing
`doStep()`.

```python
from farcel import RunControl, create_backend

control = RunControl()

def on_progress(progress):
    print(progress.current_time, progress.fraction)

result = create_backend().run_fmu(
    fmu_path,
    config,
    control=control,
    on_progress=on_progress,
)
```

The callback runs on the same thread as `run_fmu`; GUI callers must marshal it
to their UI thread themselves. A pre-start stop raises `CANCELLED`. Once
initialized, a stop returns a `STOPPED` partial `SimulationResult`, including
the final communication-point state, and that result can be exported to CSV.

## Result Chunk Streaming

`run_fmu()` can additionally deliver already-recorded result samples in bounded
`ResultChunk` batches. This is an observation API: it neither changes the FMU
communication step nor causes additional output reads, and the complete
`SimulationResult` is still returned at the end.

```python
from farcel import ResultChunk, create_backend

def on_result_chunk(chunk: ResultChunk) -> None:
    append_to_plot(chunk.time, chunk.columns)
    if chunk.final_chunk:
        finish_plot(chunk.run_id)

result = create_backend().run_fmu(
    fmu_path,
    config,
    on_result_chunk=on_result_chunk,
    result_chunk_size=256,
)
```

Chunks contain contiguous `SimulationResult` samples, including the initial
sample, rather than communication steps. Each run has a fresh `run_id` and
zero-based contiguous `sequence`; `columns` is empty when no outputs were
selected, while `time` still streams. On completed and cooperatively stopped
runs exactly one non-empty final chunk has `final_chunk=True`, after the final
sample and before terminal progress. Runtime failures do not synthesize a final
chunk. The callback runs on the `run_fmu()` thread; exceptions become
`INTERNAL_ERROR` and still use normal resource cleanup.

For a selected FMI 3 array output, a chunk column remains a sequence of nested
tuple samples, exactly matching the corresponding `SimulationResult.outputs`
column after chunks are concatenated.

## Backend API Quick Start

前端开发者从仓库根目录安装 editable package 后，可直接使用公开 API；不需要设置 `PYTHONPATH`，也不要导入 infrastructure 或调用 CLI：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

```python
from farcel import create_backend
from farcel.contracts import EngineError, SimulationConfig

backend = create_backend()
metadata = backend.load_fmu(fmu_path)
config = SimulationConfig(
    start_time=0.0,
    stop_time=2.0,
    communication_step=0.01,
    parameters={"mu": 2.0},
    selected_outputs=("x0",),
)

try:
    backend.validate_config(metadata, config)
    result = backend.run_fmu(fmu_path, config)
    first_time = result.timestamps[0]
    first_x0 = result.outputs["x0"][0]
    export = backend.export_result(result, csv_path)
except EngineError as error:
    print(error.code, error.message, error.details)
```

可运行的完整示例见 [examples/backend_api_example.py](examples/backend_api_example.py)，字段语义、错误处理和同步执行限制见 [前后端集成契约](docs/FRONTEND_BACKEND_INTEGRATION.md)。
