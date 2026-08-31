# FMU test fixtures

These files are regression fixtures. Their current Farcel status is tracked in
[`docs/FMU_COMPATIBILITY.md`](../../docs/FMU_COMPATIBILITY.md).

| File | FMI | Interface | Regression purpose | Confirmed source | Confirmed license |
|---|---|---|---|---|---|
| `Stair.fmu` | 2.0 | Co-Simulation + Model Exchange | Discrete time-event smoke test | Modelica Association Reference FMUs v0.0.41 | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `VanDerPol.fmu` | 2.0 | Co-Simulation + Model Exchange | FMI 2 inspect, parameter, run, output and CSV baseline | Modelica Association Reference FMUs v0.0.41 | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `VanDerPol-fmi3.fmu` | 3.0 | Co-Simulation + Model Exchange | Basic FMI 3 inspect, parameter, run, output and CSV baseline | Modelica Association Reference FMUs v0.0.41 | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `BouncingBall-fmi3.fmu` | 3.0 | Co-Simulation + Model Exchange | FMI 3 Event Mode and Early Return lifecycle, bounce behavior, sampling and ResultChunk regression | Modelica Association Reference FMUs v0.0.41 official `Reference-FMUs.zip`, `3.0/BouncingBall.fmu` | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `StateSpace-fmi3.fmu` | 3.0 | Co-Simulation + Model Exchange | FMI 3 default/resolved array parameter, input, output, ResultChunk and indexed CSV regression; `m`/`n`/`r` structural parameters are intentionally not overridden | Modelica Association Reference FMUs v0.0.41 official `Reference-FMUs.zip`, `3.0/StateSpace.fmu` | BSD-2-Clause; see `Reference-FMUs-LICENSE.txt` |
| `bouncingBall.fmu` | 1.0 | Model Exchange | Explicit unsupported-version/interface regression | Not confirmed; embedded source headers identify QTronic GmbH copyright | Not confirmed |
| `manipulator.fmu` | 2.0 | Co-Simulation | Native failure diagnostics and cleanup regression | `modelDescription.xml` identifies author DNV, version 0.1 | Not confirmed |
| `LateralMotionControl.fmu` | 2.0 | Co-Simulation | Initial and communication-point input regression | Embedded metadata/source identify the Bosch RTAS Challenge and Robert Bosch GmbH | AGPL-3.0 in embedded metadata and model source; bundled FMI headers are BSD-2-Clause |

The Reference FMUs listed above were obtained from:
https://github.com/modelica/Reference-FMUs/releases/tag/v0.0.41

No source or license is inferred where the FMU itself does not provide enough
evidence.
