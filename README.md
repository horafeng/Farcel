# Farcel

Farcel 是一个本地优先的轻量级 FMU 仿真工具。当前后端已支持通过 FMPy 读取 FMI 2.0 / 3.0 FMU 元数据、仿真前配置验证，以及 FMI 2.0 和基础 FMI 3.0 Co-Simulation 的 Session 执行、所选输出变量时序采样与 CSV 导出。

`communication_step` 是 FMU Co-Simulation 的通信点推进步长；`output_interval` 是保存到 `SimulationResult` 的结果采样间隔。未设置 `output_interval` 时它等于 `communication_step`，保持原有“每通信点一个样本”的行为。显式设置的采样间隔必须是通信步长的整数倍；Farcel 不插值，并会在正常完成时补记尚未采集的最终状态。

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

CSV 导出按指定路径写入 UTF-8 文件，自动创建父目录并覆盖同名文件；不会自动补充 `.csv` 扩展名。仓库内 `artifacts/` 用于本地验证输出，并已加入 Git ignore。

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
