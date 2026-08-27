# Farcel

Farcel 是一个本地优先的轻量级 FMU 仿真工具。当前后端已支持通过 FMPy 读取 FMI 2.0 / 3.0 FMU 元数据、仿真前配置验证，以及 FMI 2.0 Co-Simulation 的 Session 执行和所选输出变量时序采样。

设计说明见 [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md)。

```powershell
python -m pip install -e .
python -m unittest discover -s tests
python -m farcel.cli inspect path/to/model.fmu
python -m farcel.cli inspect path/to/model.fmu --json
python -m farcel.cli validate path/to/model.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter gain=2.0 --output value
python -m farcel.cli run path/to/model.fmu --start-time 0 --stop-time 1 --step-size 0.01 --parameter gain=2.0 --output value
```
