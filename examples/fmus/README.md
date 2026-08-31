# FMU test fixtures

These files are regression fixtures. Their current Farcel status is tracked in
[`docs/FMU_COMPATIBILITY.md`](../../docs/FMU_COMPATIBILITY.md).

| File | FMI | Interface | Regression purpose | Confirmed source | Confirmed license |
|---|---|---|---|---|---|
| `Stair.fmu` | 2.0 | Co-Simulation + Model Exchange | Discrete time-event smoke test | Modelica Association Reference FMUs v0.0.41 | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `VanDerPol.fmu` | 2.0 | Co-Simulation + Model Exchange | FMI 2 inspect, parameter, run, output and CSV baseline | Modelica Association Reference FMUs v0.0.41 | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `VanDerPol-fmi3.fmu` | 3.0 | Co-Simulation + Model Exchange | Basic FMI 3 inspect, parameter, run, output and CSV baseline | Modelica Association Reference FMUs v0.0.41 | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `BouncingBall-fmi3.fmu` | 3.0 | Co-Simulation + Model Exchange | FMI 3 Event Mode and Early Return lifecycle, bounce behavior, sampling and ResultChunk regression | Modelica Association Reference FMUs v0.0.41 official `Reference-FMUs.zip`, `3.0/BouncingBall.fmu` | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `StateSpace-fmi3.fmu` | 3.0 | Co-Simulation + Model Exchange | 官方原始二进制的 FMI 3 默认数组参数、输入、输出、ResultChunk 与带索引 CSV 回归；不用于结构参数正向运行 | Modelica Association Reference FMUs v0.0.41 官方 `Reference-FMUs.zip` 的 `3.0/StateSpace.fmu` | BSD-2-Clause；见 `Reference-FMUs-LICENSE.txt` |
| `StateSpace-fmi3-patched.fmu` | 3.0 | Co-Simulation + Model Exchange | 仅供 Phase 2.2B 回归的本地 patched fixture：结构参数、动态有效尺寸、数组输入计划、ResultChunk 和 CSV | 基于 Modelica Association Reference-FMUs v0.0.41 源码，应用 `patches/StateSpace-v0.0.41-setUInt64.patch` 后按官方 CMake 流程构建；SHA-256 `26818B9F3386DE3EF63436BF3C122264E32A4806A605216F6A269EAD3BCD2F18`；不是官方原始二进制 | BSD-2-Clause；见 `Reference-FMUs-LICENSE.txt` 与 `patches/README.md` |
| `bouncingBall.fmu` | 1.0 | Model Exchange | Explicit unsupported-version/interface regression | Not confirmed; embedded source headers identify QTronic GmbH copyright | Not confirmed |
| `manipulator.fmu` | 2.0 | Co-Simulation | Native failure diagnostics and cleanup regression | `modelDescription.xml` identifies author DNV, version 0.1 | Not confirmed |
| `LateralMotionControl.fmu` | 2.0 | Co-Simulation | Initial and communication-point input regression | Embedded metadata/source identify the Bosch RTAS Challenge and Robert Bosch GmbH | AGPL-3.0 in embedded metadata and model source; bundled FMI headers are BSD-2-Clause |

The Reference FMUs listed above were obtained from:
https://github.com/modelica/Reference-FMUs/releases/tag/v0.0.41

No source or license is inferred where the FMU itself does not provide enough
evidence.
