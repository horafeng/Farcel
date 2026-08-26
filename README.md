# Farcel

Farcel 是一个本地优先的轻量级 FMU 仿真工具。当前后端已支持通过 FMPy 读取 FMI 2.0 / 3.0 FMU 元数据和 CLI 检查；真实仿真尚未实现。

设计说明见 [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md)。

```powershell
python -m pip install -e .
python -m unittest discover -s tests
python -m farcel.cli inspect path/to/model.fmu
python -m farcel.cli inspect path/to/model.fmu --json
```
