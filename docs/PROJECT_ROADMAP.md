# Farcel 项目路线图

Farcel 的定位是**面向异构数字模型集成仿真的本地优先仿真平台原型**。本文件区分已实现、进行中和远期方向；未标为 Completed 的能力均不是当前产品承诺。

## 已完成基础

| 阶段 | 状态 | 范围 |
|---|---|---|
| Phase 1 | Completed | 单 FMU 基础：contracts、application/infrastructure 边界、导入、inspect、CLI 与稳定错误。 |
| Phase 2 | Completed | 单 FMU 工程化：FMI 2/3 Co-Simulation、inputs、selected outputs、采样间隔、FMI3 Event Mode/Early Return、arrays、structural parameters、RunControl/Progress/ResultChunk/CSV。 |

## 已完成：单模型 Model Exchange

**Phase 3 — Completed。** FMI2 `ModelExchangeSession`、CVode SolverAdapter、event coordinator 与 public `ModelExchangeRunner` 已完成 Reference FMU release hardening：continuous/state/time/input event、零状态、repeat、temp cleanup 与 Issue #882 均有回归保护。`run_fmu`、validation、metadata 与 CLI 都支持可执行的 FMI 2 Model Exchange；默认仍优先 Co-Simulation。FMI 3 Model Exchange 与 Scheduled Execution 仍未实现。

## 已完成：本地多模型集成

**Phase 4 — Completed。** 已交付 graph contracts、metadata validation、Co-Simulation / FMI2 Model Exchange node runtimes、explicit-Jacobi scheduler/data router、`GraphSimulationResult`、global control/progress/cleanup、真实 FMI2/FMI3 multi-FMU regressions，以及 public `validate_graph()` / `run_graph()`。本机 graph 仍是同步、single-machine、previous-checkpoint coupling；它不包含 project persistence 或 GUI graph editor。

**Phase 5 — Completed。** 后端 **Simulation Project / Engineering Management** layer 已交付 `SimulationProject` / `ModelAsset` / `SimulationCase` DTO、local JSON `project.json` repository、portable relative model paths、SHA-256 asset integrity、relocation、`ProjectValidator`、`ProjectService`、result provenance artifact、safe `results/<run_id>.json` repository、RunHistory 与 artifact-first persistence。默认 backend 公开 `open_project`、`save_project`、`validate_project`、`run_project_case` 和 `load_project_run`；历史结果可在当前 FMU 或 case 后续变化时按 artifact provenance 恢复。后端不负责 PySide6 graph canvas，也不意味着已经支持任意第三方工具的直接连接。

Phase 5 的后端职责是 project definition、persistence、model assets、cases、run history、project validation 和 public persistent case execution。前端职责是 PySide6 graph editor、canvas、blocks、connections、scope 和 project UI。Project 位于现有 `SimulationGraph` 之上；它不改变 Phase 4 的数值语义、validator、router、orchestrator 或 node runtime。

## 已规划：场景批量与结果后处理

**Phase 6 — Scenario Batch & Result Post-processing — Planned / Design frozen after 6.0。** 在不改变既有 Graph runtime、project provenance 或 persistence 语义的前提下，后续子阶段将交付 canonical GraphSimulationResult / historical artifact CSV export、调用既有 run_project_case() 的本地同步串行 case batch，以及基于 immutable artifacts 的历史结果比较和基础标量统计。它不引入新的 project runner、solver、graph scheduler、parallel/distributed runtime 或 frontend 实现。冻结设计见 [PHASE_6_SCENARIO_BATCH_RESULT_POSTPROCESSING_DESIGN.md](PHASE_6_SCENARIO_BATCH_RESULT_POSTPROCESSING_DESIGN.md)。

## Later / Long-term direct-tool integration

Simulink、AMESim、ANSYS 等 direct adapter 仍是远期候选。异构模型当前优先通过 FMU 接入；只有在具备明确的可行性、验证和维护能力时，才单独规划这些 proprietary direct-tool adapters。它们没有被取消，但不再占用紧接 Phase 5 的 Phase 6 编号。

## 其他远期方向

**Phase 7 — Long-term。** 在本地 `SimulationGraph` 和单机调度可靠后，再评估分布式执行；当前没有 worker、RPC、云服务或分布式仿真。

**Phase 8 — Long-term optional。** 实时/HIL、ROM 和性能导向的 native worker/C++ 加速仅在相应需求与本地基线成熟后单独设计。它们目前不存在，也不由当前 Python orchestration 伪装实现。

## 目标架构

```text
GUI / CLI → FarcelEngine.run_graph → GraphValidator → GraphSimulationRunner
    → GraphRuntimeBindingsFactory → DataRouter / SimulationOrchestrator
    → FMU CS / FMI2 ME node runtimes
```

Phase 5 已在不改变上述 Graph runtime 的前提下增加一层：

```text
GUI / CLI → FarcelEngine / ProjectService → SimulationProject → SimulationCase
    → resolved SimulationGraph → existing validate_graph / run_graph
```

`ProjectService`、project persistence 与 public project API 均已实现；完整 public handoff 见 [FRONTEND_BACKEND_INTEGRATION.md](FRONTEND_BACKEND_INTEGRATION.md) 与 [../examples/project_api_example.py](../examples/project_api_example.py)。

当前架构仍为：

```text
GUI / CLI → application → contracts ← infrastructure
```

GUI 技术路线为 Python + PySide6，结果展示可使用 PySide6 + matplotlib；application 由 Python 编排，FMPy 与 native FMU 细节隔离在 infrastructure。未来 solver adapter、native worker 或 C++ 实现只能替换/补充 infrastructure，不能使 GUI 或 public contracts 依赖 FMPy/native handle。
