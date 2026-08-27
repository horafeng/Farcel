# Farcel

Farcel 是一个本地优先的轻量级 FMU 仿真工具。当前后端已支持通过 FMPy 读取 FMI 2.0 / 3.0 FMU 元数据、仿真前配置验证，以及 FMI 2.0 Co-Simulation 的 Session 执行和所选输出变量时序采样。

设计说明见 [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md)。

## 本地开发

在仓库根目录创建项目虚拟环境并安装 editable package：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

日常命令直接使用项目虚拟环境的 Python，不要求执行 `Activate.ps1`：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m farcel.cli inspect .\examples\fmus\VanDerPol.fmu
.\.venv\Scripts\python.exe -m farcel.cli inspect .\examples\fmus\VanDerPol.fmu --json
.\.venv\Scripts\python.exe -m farcel.cli validate .\examples\fmus\VanDerPol.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter "mu=2.0" --output x0
.\.venv\Scripts\python.exe -m farcel.cli run .\examples\fmus\VanDerPol.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter "mu=2.0" --output x0
.\.venv\Scripts\python.exe -m farcel.cli export .\examples\fmus\VanDerPol.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter "mu=2.0" --output x0 --csv .\artifacts\VanDerPol.csv
```

如果本机策略允许，也可以自行激活 `.venv`，但 Farcel 的安装和验证不依赖虚拟环境激活。

CSV 导出按指定路径写入 UTF-8 文件，自动创建父目录并覆盖同名文件；不会自动补充 `.csv` 扩展名。仓库内 `artifacts/` 用于本地验证输出，并已加入 Git ignore。
